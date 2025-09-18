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
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Try synchronous processing first (40 second timeout)
        try:
            async with asyncio.timeout(40):
                # Quick analysis for small content
                if len(content) < 10000:  # Small content threshold
                    chunks = chunker.chunk_content(content, request.user_prompt)
                    if len(chunks) == 1:  # Single chunk - try sync
                        try:
                            result = await claude_service.process_chunk(chunks[0], request)
                            
                            # ADD QUALITY ASSESSMENT TO SYNC PATH TOO (consistency with async path)
                            quality_status = await claude_service.assess_quality(result, request)
                            analysis_name = await claude_service.generate_analysis_name(result, request)
                            
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
                            logger.warning(f"Sync processing failed, falling back to async: {sync_error}")
                            # Fall through to async processing
                            pass
        except asyncio.TimeoutError:
            logger.info("Sync processing timed out, falling back to async")
            pass  # Fall through to async processing
        except Exception as e:
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
    """Get current queue status with lightweight calculation"""
    try:
        # Count pending jobs (fast)
        pending_count = job_queue.redis.llen(job_queue.job_queue_key)
        
        # Count processing jobs (fast)
        processing_count = job_queue.redis.scard(job_queue.processing_key)
        
        # LIGHTWEIGHT WAIT TIME CALCULATION (no expensive scans)
        estimated_wait = 0
        if pending_count > 0:
            # Simple tiered estimates - no Redis scanning
            if pending_count <= 2:
                estimated_wait = pending_count * 1.5  # Small jobs: 1.5 min each
            elif pending_count <= 5:
                estimated_wait = pending_count * 2.5  # Medium load: 2.5 min each
            elif pending_count <= 10:
                estimated_wait = pending_count * 3.5  # Higher load: 3.5 min each
            else:
                estimated_wait = pending_count * 5    # High load: 5 min each
        
        # Add small buffer if jobs are currently running
        if processing_count > 0 and pending_count > 0:
            estimated_wait += 0.5  # Add 30 second buffer
        
        return {
            "queue_length": pending_count,
            "currently_processing": processing_count,
            "estimated_wait_minutes": max(0, round(estimated_wait, 1)),
            "status": "operational" if pending_count < 10 else "busy"
        }
    except Exception as e:
        logger.error(f"Queue status check failed: {e}")
        return {
            "status": "unknown", 
            "error": str(e),
            "queue_length": 0,
            "currently_processing": 0,
            "estimated_wait_minutes": 0
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