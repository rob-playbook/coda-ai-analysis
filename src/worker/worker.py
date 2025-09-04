# src/worker/worker.py
import asyncio
import logging
import time
import signal
import sys
import aiohttp
import os

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
        
        # Get webhook configuration from environment
        self.coda_webhook_url = os.environ.get('CODA_WEBHOOK_URL')
        self.coda_api_token = os.environ.get('CODA_API_TOKEN')
        
        if self.coda_webhook_url:
            logger.info("Webhook notifications enabled")
        else:
            logger.info("Webhook notifications disabled - polling only mode")
        
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
    
    def _has_processing_errors(self, results: list) -> bool:
        """Check if any results contain error messages"""
        for result in results:
            if result.startswith("[Error processing chunk"):
                return True
            if "Error code:" in result:
                return True
            if "error" in result.lower() and len(result.strip()) < 200:  # Short error messages
                return True
            if len(result.strip()) < 10:  # Suspiciously short
                return True
        return False
    
    def _extract_error_message(self, results: list) -> str:
        """Extract error message from results"""
        errors = []
        for result in results:
            if result.startswith("[Error processing chunk") or "Error code:" in result:
                errors.append(result)
        return "; ".join(errors) if errors else "Unknown processing error"
    
    async def process_job(self, job: AnalysisJob):
        """Process a single analysis job
        
        BACKWARD COMPATIBILITY: Quality assessment failures are embedded in the 
        analysis_result field rather than changing status codes. This ensures 
        existing Coda CheckResults buttons continue to work without modification.
        """
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
            
            # Step 3: Check for processing errors BEFORE quality assessment
            if self._has_processing_errors(results):
                # Immediate failure for processing errors - don't waste API calls on quality assessment
                quality_status = "FAILED"
                analysis_name = "Processing Error"
                error_message = self._extract_error_message(results)
                
                logger.error(f"Job {job.job_id} failed due to processing errors: {error_message}")
                
                # Store error result
                processing_time = time.time() - start_time
                final_result = AnalysisResult(
                    record_id=request_data.record_id,
                    status="FAILED",
                    error_message=f"Analysis failed during processing: {error_message}",
                    analysis_name="Processing Error",
                    processing_stats={
                        "job_id": job.job_id,
                        "chunk_count": chunk_count,
                        "processing_time_seconds": round(processing_time, 2),
                        "failure_reason": "processing_error"
                    }
                )
            else:
                # Step 3: Combine results
                combined_result = self._combine_chunk_results(results)
                
                # Step 3.5: Ensure format consistency for multi-chunk results
                if chunk_count > 1:
                    logger.info(f"Ensuring format consistency for {chunk_count} chunks")
                    before_length = len(combined_result)
                    logger.info(f"Before consistency check: {before_length} characters")
                    combined_result = await self.claude_service.ensure_format_consistency(combined_result, request_data)
                    after_length = len(combined_result)
                    logger.info(f"After consistency check: {after_length} characters (diff: {after_length - before_length})") 
                
                # Step 4: Quality assessment (only for successful processing)
                quality_status = await self.claude_service.assess_quality(combined_result, request_data)
                
                # Step 5: Generate analysis name (only for successful processing) 
                analysis_name = await self.claude_service.generate_analysis_name(combined_result, request_data)
                
                # Store result - use actual quality status and Claude's response as error message
                processing_time = time.time() - start_time
                final_result = AnalysisResult(
                    record_id=request_data.record_id,
                    status=quality_status,
                    analysis_result=combined_result,
                    analysis_name="Quality Check Failed" if quality_status == "FAILED" else analysis_name,
                    error_message=combined_result if quality_status == "FAILED" else None,
                    processing_stats={
                        "job_id": job.job_id,
                        "chunk_count": chunk_count,
                        "total_characters": len(combined_result),
                        "processing_time_seconds": round(processing_time, 2),
                        "quality_status": quality_status
                    }
                )
            
            # Step 6: Store result for polling access
            self.job_queue.store_result(job.job_id, final_result)
            
            # Send notification webhook to Coda with actual quality status
            webhook_success = True
            if self.coda_webhook_url and self.coda_api_token:
                webhook_success = await self._send_coda_webhook_notification(job.job_id, quality_status)
            
            # Handle legacy webhook if provided in request (BACKWARD COMPATIBILITY)
            if hasattr(request_data, 'webhook_url') and request_data.webhook_url and request_data.webhook_url.strip():
                legacy_webhook_success = await self._send_legacy_webhook(request_data.webhook_url, final_result)
                if not legacy_webhook_success:
                    webhook_success = False
            
            # Complete or retry job based on webhook success
            if webhook_success:
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
                
                # Store error result and try to send error webhook
                try:
                    error_result = AnalysisResult(
                        record_id=job.request_data.record_id,
                        status="FAILED",
                        error_message=error_message,
                        processing_stats={"job_id": job.job_id, "error": True}
                    )
                    # Always store result for polling
                    self.job_queue.store_result(job.job_id, error_result)
                    
                    # Send notification webhook for failed job
                    if self.coda_webhook_url and self.coda_api_token:
                        await self._send_coda_webhook_notification(job.job_id, "FAILED")
                        
                except Exception as webhook_error:
                    logger.error(f"Failed to send error webhook: {webhook_error}")
    
    def _combine_chunk_results(self, results: list) -> str:
        """Combine chunk results with clean separators for consistency processing"""
        if len(results) == 1:
            return results[0]
        
        # Simple joining - let consistency check handle proper merging
        return "\n\n".join(results)
    
    async def _send_coda_webhook_notification(self, job_id: str, status: str, max_retries: int = 3) -> bool:
        """
        Send simple notification webhook to Coda automation
        Just notifies that analysis is complete - Coda fetches data via CheckResults
        """
        if not self.coda_webhook_url or not self.coda_api_token:
            logger.warning("Coda webhook URL or API token not configured")
            return True  # Don't fail job if webhook not configured
        
        for attempt in range(max_retries):
            try:
                # Simple notification payload - just job_id and status
                notification_payload = {
                    "job_id": job_id,
                    "status": "complete" if status == "SUCCESS" else "failed"
                }
                
                timeout = aiohttp.ClientTimeout(total=30)
                headers = {
                    "Authorization": f"Bearer {self.coda_api_token}",
                    "Content-Type": "application/json"
                }
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        self.coda_webhook_url,
                        json=notification_payload,
                        headers=headers
                    ) as response:
                        if response.status in [200, 202]:  # Accept both OK and Accepted
                            logger.info(f"Coda webhook notification sent successfully for job {job_id}")
                            return True
                        else:
                            response_text = await response.text()
                            logger.warning(f"Coda webhook failed with status {response.status}: {response_text}, attempt {attempt + 1}")
                            
            except Exception as e:
                logger.error(f"Coda webhook error (attempt {attempt + 1}): {e}")
            
            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                await asyncio.sleep(wait_time)
        
        logger.error(f"Coda webhook notification failed for job {job_id} after {max_retries} attempts")
        return False
    
    async def _send_legacy_webhook(self, webhook_url: str, result: AnalysisResult, max_retries: int = 3) -> bool:
        """Send legacy webhook with full data (for backward compatibility)"""
        for attempt in range(max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    payload = result.dict()
                    
                    async with session.post(webhook_url, json=payload) as response:
                        if response.status == 200:
                            logger.info(f"Legacy webhook sent successfully for record {result.record_id}")
                            return True
                        else:
                            logger.warning(f"Legacy webhook failed with status {response.status}, attempt {attempt + 1}")
                            
            except Exception as e:
                logger.error(f"Legacy webhook error (attempt {attempt + 1}): {e}")
            
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
