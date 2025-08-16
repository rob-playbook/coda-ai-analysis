# src/worker/job_queue.py
import redis
import json
import logging
import time
from typing import Optional
from src.shared.models import AnalysisJob, JobStatus

logger = logging.getLogger(__name__)

class JobQueue:
    def __init__(self, redis_url: str):
        # Debug: log the URL being used
        logger.info(f"Redis URL: {redis_url[:20]}...")
        
        # Handle SSL connections for Upstash
        if redis_url.startswith('rediss://'):
            logger.info("Using SSL connection")
            self.redis = redis.from_url(redis_url, decode_responses=True, ssl_cert_reqs=None)
        else:
            logger.info("Using regular connection")
            self.redis = redis.from_url(redis_url, decode_responses=True)
        self.job_queue_key = "analysis_jobs"
        self.job_data_key = "job_data:{job_id}"
        self.processing_key = "processing_jobs"
        
    async def ping(self) -> bool:
        """Test queue connectivity"""
        try:
            return self.redis.ping()
        except Exception as e:
            logger.error(f"Queue ping failed: {e}")
            return False
    
    async def enqueue_job(self, job: AnalysisJob) -> bool:
        """Add job to queue"""
        try:
            # Store job data
            job_key = self.job_data_key.format(job_id=job.job_id)
            self.redis.setex(job_key, 86400, job.json())  # 24 hour expiry
            
            # Add to processing queue
            self.redis.lpush(self.job_queue_key, job.job_id)
            
            logger.info(f"Job queued: {job.job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue job {job.job_id}: {e}")
            return False
    
    async def dequeue_job(self) -> Optional[AnalysisJob]:
        """Get next job from queue"""
        try:
            # Blocking pop with timeout
            result = self.redis.brpop(self.job_queue_key, timeout=30)
            if not result:
                return None
            
            job_id = result[1]
            
            # Get job data
            job_key = self.job_data_key.format(job_id=job_id)
            job_data = self.redis.get(job_key)
            
            if not job_data:
                logger.warning(f"Job data not found for {job_id}")
                return None
            
            job = AnalysisJob.parse_raw(job_data)
            
            # Mark as processing
            job.status = JobStatus.PROCESSING
            job.started_at = time.time()
            self.redis.setex(job_key, 86400, job.json())
            self.redis.sadd(self.processing_key, job_id)
            
            return job
        except Exception as e:
            logger.error(f"Failed to dequeue job: {e}")
            return None
    
    async def complete_job(self, job: AnalysisJob) -> bool:
        """Mark job as completed"""
        try:
            job.completed_at = time.time()
            job_key = self.job_data_key.format(job_id=job.job_id)
            self.redis.setex(job_key, 86400, job.json())
            self.redis.srem(self.processing_key, job.job_id)
            
            logger.info(f"Job completed: {job.job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to complete job {job.job_id}: {e}")
            return False
    
    async def fail_job(self, job: AnalysisJob, error_message: str) -> bool:
        """Mark job as failed"""
        try:
            job.status = JobStatus.FAILED
            job.error_message = error_message
            job.completed_at = time.time()
            
            job_key = self.job_data_key.format(job_id=job.job_id)
            self.redis.setex(job_key, 86400, job.json())
            self.redis.srem(self.processing_key, job.job_id)
            
            logger.error(f"Job failed: {job.job_id} - {error_message}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark job as failed {job.job_id}: {e}")
            return False
    
    async def get_job(self, job_id: str) -> Optional[AnalysisJob]:
        """Get job by ID"""
        try:
            job_key = self.job_data_key.format(job_id=job_id)
            job_data = self.redis.get(job_key)
            
            if not job_data:
                return None
            
            return AnalysisJob.parse_raw(job_data)
        except Exception as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None