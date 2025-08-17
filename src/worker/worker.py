# src/worker/worker.py
import asyncio
import logging
import time
import signal
import sys
import aiohttp

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.worker.job_queue import JobQueue
from src.worker.chunking import ContentChunker
from src.worker.claude import ClaudeService
from src.shared.config import get_settings
from src.shared.logging import setup_logging
from src.shared.models import AnalysisJob, JobStatus, AnalysisResult

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

class AnalysisWorker:
    def __init__(self):
        self.settings = get_settings()
        self.job_queue = JobQueue(self.settings.queue_url)
        self.claude_service = ClaudeService(self.settings.claude_api_key)
        self.chunker = ContentChunker()
        self.running = True
        
    async def start(self):
        """Start the worker process"""
        logger.info("Starting analysis worker...")
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        # Main worker loop
        while self.running:
            try:
                # Get next job from queue
                job = self.job_queue.dequeue_job()
                
                if job:
                    await self.process_job(job)
                else:
                    # No job available, sleep briefly
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(5)  # Wait before retrying
        
        logger.info("Analysis worker stopped")
    
    async def process_job(self, job: AnalysisJob):
        """Process a single analysis job"""
        logger.info(f"Processing job {job.job_id} for record {job.record_id}")
        start_time = time.time()
        
        try:
            request_data = job.request_data
            
            # Step 1: Chunk content
            chunks = self.chunker.chunk_content(
                request_data.content,
                request_data.user_prompt
            )
            
            chunk_count = len(chunks)
            logger.info(f"Content split into {chunk_count} chunks for job {job.job_id}")
            
            # Step 2: Process chunks through Claude API
            results = await self.claude_service.process_chunks_sequential(
                chunks, request_data
            )
            
            # Step 3: Combine results
            combined_result = self._combine_chunk_results(results)
            
            # Step 4: Quality assessment
            quality_status = await self.claude_service.assess_quality(combined_result)
            
            # Step 5: Generate analysis name
            analysis_name = await self.claude_service.generate_analysis_name(combined_result)
            
            # Step 6: Send results to Coda
            processing_time = time.time() - start_time
            final_result = AnalysisResult(
                record_id=request_data.record_id,
                status=quality_status,
                analysis_result=combined_result,
                analysis_name=analysis_name,
                processing_stats={
                    "job_id": job.job_id,
                    "chunk_count": chunk_count,
                    "total_characters": len(combined_result),
                    "processing_time_seconds": round(processing_time, 2)
                }
            )
            
            success = await self._send_webhook(request_data.webhook_url, final_result)
            
            if success:
                job.status = JobStatus.SUCCESS
                self.job_queue.complete_job(job)
                logger.info(f"Job {job.job_id} completed successfully in {processing_time:.2f}s")
            else:
                # Webhook failed - retry job if possible
                if job.retry_count < job.max_retries:
                    self.job_queue.retry_job(job)
                    logger.warning(f"Job {job.job_id} webhook failed, queued for retry")
                else:
                    self.job_queue.fail_job(job, "Webhook delivery failed after max retries")
                    logger.error(f"Job {job.job_id} failed - webhook delivery failed")
            
        except Exception as e:
            error_message = f"Job processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} error: {error_message}")
            
            # Try to retry job if possible
            if job.retry_count < job.max_retries:
                self.job_queue.retry_job(job)
                logger.info(f"Job {job.job_id} queued for retry (attempt {job.retry_count + 1})")
            else:
                self.job_queue.fail_job(job, error_message)
                
                # Try to send error webhook to Coda
                try:
                    error_result = AnalysisResult(
                        record_id=job.request_data.record_id,
                        status="FAILED",
                        error_message=error_message,
                        processing_stats={"job_id": job.job_id, "error": True}
                    )
                    await self._send_webhook(job.request_data.webhook_url, error_result)
                except Exception as webhook_error:
                    logger.error(f"Failed to send error webhook: {webhook_error}")
    
    def _combine_chunk_results(self, results: list) -> str:
        """Combine chunk results maintaining readability and context"""
        if len(results) == 1:
            return results[0]
        
        # For multiple chunks, combine with clear separators
        separator = "\n\n" + "="*50 + " CHUNK SEPARATOR " + "="*50 + "\n\n"
        header = "="*50 + " COMBINED ANALYSIS RESULTS " + "="*50 + "\n\n"
        return header + separator.join(results)
    
    async def _send_webhook(self, webhook_url: str, result: AnalysisResult, max_retries: int = 3) -> bool:
        """Send result to Coda via webhook with retry logic"""
        for attempt in range(max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    payload = result.dict()
                    
                    async with session.post(webhook_url, json=payload) as response:
                        if response.status == 200:
                            logger.info(f"Webhook sent successfully for record {result.record_id}")
                            return True
                        else:
                            logger.warning(f"Webhook failed with status {response.status}, attempt {attempt + 1}")
                            
            except Exception as e:
                logger.error(f"Webhook error (attempt {attempt + 1}): {e}")
            
            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                await asyncio.sleep(wait_time)
        
        return False
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

async def main():
    """Main entry point for the worker"""
    worker = AnalysisWorker()
    await worker.start()

if __name__ == "__main__":
    asyncio.run(main())