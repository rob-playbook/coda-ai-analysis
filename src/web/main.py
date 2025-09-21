# src/web/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uuid
import time
import logging
import asyncio

from src.shared.models import AnalysisRequest, JobStatus, AnalysisJob, PollingRequest, AnalysisResult
from src.shared.config import get_settings
from src.shared.logging import setup_logging
from src.worker.job_queue import JobQueue
from src.worker.chunking import ContentChunker
from src.worker.claude import ClaudeService
from src.worker.file_processor import FileProcessor

setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Coda AI Analysis Service",
    description="Render-based service for processing large content through Claude API",
    version="1.0.0"
)

# CORS middleware for Coda integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://coda.io", "https://*.coda.io"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Initialize services
job_queue = JobQueue(settings.queue_url)
claude_service = ClaudeService(settings.claude_api_key)
chunker = ContentChunker()
file_processor = FileProcessor()

@app.get("/health")
async def health_check():
    """Health check endpoint for Render monitoring"""
    try:
        # Test queue connectivity
        job_queue.ping()
        return {
            "status": "healthy", 
            "service": "coda-ai-analysis-web",
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")

# =================== POLLING ENDPOINTS ===================

@app.post("/request")
async def start_analysis(request: PollingRequest):
    """
    NEW: Start analysis - try synchronous first, fallback to async
    """
    try:
        # Reconstruct content from split pieces
        content = request.reconstruct_content()
        
        # Validate request
        if not content or len(content.strip()) == 0:
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
        if not request.user_prompt or len(request.user_prompt.strip()) == 0:
            raise HTTPException(status_code=400, detail="User prompt cannot be empty")
        
        if len(content) > settings.max_content_size:
            raise HTTPException(
                status_code=400, 
                detail=f"Content exceeds maximum size of {settings.max_content_size} characters"
            )
        
        # DETECT FILE PROCESSING vs TEXT PROCESSING
        is_file_request = content.startswith("FILE_URL:")
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # FILE PROCESSING PATH
        if is_file_request:
            logger.info(f"File processing detected for job {job_id}")
            logger.info(f"Raw file content: {content[:200]}...")  # Debug the raw content
            
            # Extract file URLs
            file_urls = file_processor.extract_file_urls(content)
            logger.info(f"Extracted {len(file_urls)} file URLs: {file_urls}")
            
            if not file_urls:
                logger.error(f"No valid file URLs found. Raw content was: {content}")
                raise HTTPException(status_code=400, detail="No valid file URLs found in request")
            
            logger.info(f"Processing {len(file_urls)} files: {[url[:50] + '...' for url in file_urls]}")
            
            # Files always go to async processing due to download overhead
            job = AnalysisJob(
                job_id=job_id,
                record_id=request.record_id,
                status=JobStatus.PENDING,
                request_data=request.to_analysis_request(),
                created_at=time.time()
            )
            
            job_queue.enqueue_job(job)
            
            return {
                "job_id": job_id,
                "status": "processing",
                "message": f"File analysis queued for background processing ({len(file_urls)} files)",
                "estimated_time": "3-15 minutes for file processing",
                "file_count": len(file_urls)
            }
        
        # TEXT PROCESSING PATH
        # Try synchronous processing first (40 second timeout)
        try:
            async with asyncio.timeout(40):
                # Quick analysis for small content
                if len(content) < 10000:  # Small content threshold
                    chunks = chunker.chunk_content(content, request.user_prompt)
                    if len(chunks) == 1:  # Single chunk - try sync
                        try:
                            # TRACK SYNC PROCESSING
                            job_queue.redis.incr("sync_processing")
                            job_queue.redis.expire("sync_processing", 300)  # 5 min expiry
                            
                            result = await claude_service.process_chunk(chunks[0], request)
                            
                            # ADD QUALITY ASSESSMENT TO SYNC PATH TOO (consistency with async path)
                            quality_status = await claude_service.assess_quality(result, request)
                            analysis_name = await claude_service.generate_analysis_name(result, request)
                            
                            # UNTRACK SYNC PROCESSING
                            job_queue.redis.decr("sync_processing")
                            
                            # Handle failed quality assessment by returning actual Claude response as error
                            if quality_status == "FAILED":
                                # Store result with Claude's actual response as error message
                                sync_result = AnalysisResult(
                                    record_id=request.record_id,
                                    status="FAILED",
                                    analysis_result=result,  # Claude's actual response
                                    analysis_name="Quality Check Failed",
                                    error_message=result,  # Claude's actual response explaining why it failed
                                    processing_stats={
                                        "job_id": job_id,
                                        "processing_time_seconds": "immediate",
                                        "sync_completion": True,
                                        "quality_status": quality_status
                                    }
                                )
                                job_queue.store_result(job_id, sync_result)
                                
                                return {
                                    "job_id": job_id,
                                    "status": "failed",
                                    "error_message": result,  # Claude's actual response explaining the issue
                                    "analysis_result": result,
                                    "analysis_name": "Quality Check Failed",
                                    "processing_time_seconds": "immediate"
                                }
                            
                            # Quality assessment passed - normal success path
                            sync_result = AnalysisResult(
                                record_id=request.record_id,
                                status="SUCCESS",
                                analysis_result=result,
                                analysis_name=analysis_name,
                                processing_stats={
                                    "job_id": job_id,
                                    "processing_time_seconds": "immediate",
                                    "sync_completion": True,
                                    "quality_status": quality_status
                                }
                            )
                            job_queue.store_result(job_id, sync_result)
                            
                            return {
                                "job_id": job_id,
                                "status": "complete",
                                "analysis_result": result,
                                "analysis_name": analysis_name,
                                "processing_time_seconds": "immediate"
                            }
                        except Exception as sync_error:
                            # UNTRACK SYNC PROCESSING ON ERROR
                            job_queue.redis.decr("sync_processing")
                            logger.warning(f"Sync processing failed, falling back to async: {sync_error}")
                            # Fall through to async processing
                            pass
        except asyncio.TimeoutError:
            # UNTRACK SYNC PROCESSING ON TIMEOUT
            job_queue.redis.decr("sync_processing")
            logger.info("Sync processing timed out, falling back to async")
            pass  # Fall through to async processing
        except Exception as e:
            # UNTRACK SYNC PROCESSING ON ERROR
            job_queue.redis.decr("sync_processing")
            logger.warning(f"Sync processing error, falling back to async: {e}")
            pass  # Fall through to async processing
        
        # Async processing for large content or timeout
        job = AnalysisJob(
            job_id=job_id,
            record_id=request.record_id,
            status=JobStatus.PENDING,
            request_data=request.to_analysis_request(),  # Convert to AnalysisRequest
            created_at=time.time()
        )
        
        # Queue for background processing
        job_queue.enqueue_job(job)
        
        return {
            "job_id": job_id,
            "status": "processing",
            "message": "Analysis queued for background processing",
            "estimated_time": "2-10 minutes depending on content size"
        }
        
    except Exception as e:
        logger.error(f"Analysis request failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/response/{job_id}")
async def get_analysis_result(job_id: str):
    """
    Get analysis results by job ID
    
    CRITICAL: Uses actual quality assessment result, not hardcoded "complete".
    This ensures failed quality assessments are properly returned as "failed".
    """
    try:
        # First check if we have a stored result (works for both sync and async)
        result = job_queue.get_job_result(job_id)
        if result:
            return {
                "job_id": job_id,
                "status": "complete" if result.status == "SUCCESS" else "failed",
                "analysis_result": result.analysis_result,
                "analysis_name": result.analysis_name,
                "error_message": result.error_message,
                "processing_stats": result.processing_stats
            }
        
        # No stored result, check if job exists in queue (async jobs)
        job = job_queue.get_job(job_id)
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if job.status == JobStatus.SUCCESS:
            # Job marked success but no result stored - data issue
            return {
                "job_id": job_id,
                "status": "failed",
                "error_message": "Analysis completed but result data not found"
            }
        elif job.status == JobStatus.FAILED:
            return {
                "job_id": job_id,
                "status": "failed",
                "error_message": job.error_message or "Analysis failed"
            }
        else:
            # Still processing
            return {
                "job_id": job_id,
                "status": "processing",
                "message": "Analysis still in progress"
            }
            
    except HTTPException:
        raise  # Re-raise HTTP exceptions
    except Exception as e:
        logger.error(f"Result retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =================== WEBHOOK ENDPOINTS (EXISTING) ===================

@app.post("/analyze")
async def process_analysis(request: AnalysisRequest):
    """Main analysis endpoint - queues job for background processing"""
    try:
        # Validate request
        if not request.content or len(request.content.strip()) == 0:
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
        if not request.user_prompt or len(request.user_prompt.strip()) == 0:
            raise HTTPException(status_code=400, detail="User prompt cannot be empty")
        
        if not request.webhook_url:
            raise HTTPException(status_code=400, detail="Webhook URL required")
        
        if len(request.content) > settings.max_content_size:
            raise HTTPException(
                status_code=400, 
                detail=f"Content exceeds maximum size of {settings.max_content_size} characters"
            )
        
        # Create job
        job_id = str(uuid.uuid4())
        job = AnalysisJob(
            job_id=job_id,
            record_id=request.record_id,
            status=JobStatus.PENDING,
            request_data=request,
            created_at=time.time()
        )
        
        # Queue job for background processing
        job_queue.enqueue_job(job)
        
        logger.info(f"Analysis job queued: {job_id} for record {request.record_id}")
        
        # Return immediate response
        return {
            "job_id": job_id,
            "record_id": request.record_id,
            "status": "queued",
            "message": "Analysis queued for background processing",
            "estimated_time": "2-10 minutes depending on content size"
        }
        
    except Exception as e:
        logger.error(f"Analysis request failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """Check job status (for debugging/monitoring)"""
    try:
        job = job_queue.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return {
            "job_id": job_id,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "retry_count": job.retry_count
        }
    except Exception as e:
        logger.error(f"Job status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/queue/status")
async def get_queue_status():
    """Get current queue status - shows ALL analyses ahead of you"""
    try:
        # Count async jobs waiting in queue
        async_queue_count = job_queue.redis.llen(job_queue.job_queue_key)
        
        # Count async jobs currently processing 
        async_processing_count = job_queue.redis.scard(job_queue.processing_key)
        
        # Count sync jobs currently processing
        sync_processing_count = int(job_queue.redis.get("sync_processing") or 0)
        
        # TOTAL ANALYSES AHEAD OF YOU
        total_ahead = async_queue_count + async_processing_count + sync_processing_count
        
        # Estimate wait time based on total load
        estimated_wait = 0
        if total_ahead > 0:
            # Simple tiered estimates based on total system load
            if total_ahead <= 2:
                estimated_wait = total_ahead * 1.5  # Light load: 1.5 min each
            elif total_ahead <= 5:
                estimated_wait = total_ahead * 2.5  # Medium load: 2.5 min each
            elif total_ahead <= 10:
                estimated_wait = total_ahead * 3.5  # Higher load: 3.5 min each
            else:
                estimated_wait = total_ahead * 5    # High load: 5 min each
        
        return {
            "analyses_ahead": total_ahead,  # This is what users care about!
            "estimated_wait_minutes": max(0, round(estimated_wait, 1)),
            "status": "operational" if total_ahead < 10 else "busy",
            # Detailed breakdown (for debugging)
            "breakdown": {
                "sync_processing": sync_processing_count,
                "async_processing": async_processing_count, 
                "async_queued": async_queue_count
            }
        }
    except Exception as e:
        logger.error(f"Queue status check failed: {e}")
        return {
            "analyses_ahead": 0,
            "estimated_wait_minutes": 0,
            "status": "unknown", 
            "error": str(e)
        }

@app.get("/queue/user/{record_id_prefix}")
async def get_user_queue_position(record_id_prefix: str):
    """Check if user has jobs in queue"""
    try:
        # Get all job IDs in queue
        job_ids = job_queue.redis.lrange(job_queue.job_queue_key, 0, -1)
        
        user_positions = []
        for i, job_id in enumerate(job_ids):
            job = job_queue.get_job(job_id)
            if job and job.record_id.startswith(record_id_prefix):
                user_positions.append({
                    "job_id": job_id,
                    "position": i + 1,
                    "record_id": job.record_id
                })
        
        return {
            "user_jobs_in_queue": len(user_positions),
            "positions": user_positions,
            "total_queue_length": len(job_ids)
        }
    except Exception as e:
        logger.error(f"User queue check failed: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)