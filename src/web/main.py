# src/web/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uuid
import time
import logging

from ..shared.models import AnalysisRequest, JobStatus, AnalysisJob
from ..shared.config import get_settings
from ..shared.logging import setup_logging
from ..worker.queue import JobQueue

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

# Initialize job queue
job_queue = JobQueue(settings.queue_url)

@app.get("/health")
async def health_check():
    """Health check endpoint for Render monitoring"""
    try:
        # Test queue connectivity
        await job_queue.ping()
        return {
            "status": "healthy", 
            "service": "coda-ai-analysis-web",
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")

@app.post("/analyze")
async def process_analysis(request: AnalysisRequest):
    """Main analysis endpoint - queues job for background processing"""
    try:
        # Validate request
        if not request.content or len(request.content.strip()) == 0:
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
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
        await job_queue.enqueue_job(job)
        
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
        job = await job_queue.get_job(job_id)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)