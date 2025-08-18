# Updated claude.py - Uses pre-built prompts from Coda
import anthropic
from typing import Dict, Any, List
import asyncio
import logging
import time
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class ClaudeService:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        reraise=True
    )
    async def process_chunk(self, chunk_content: str, request_data: Any) -> str:
        """
        Process single chunk through Claude API using pre-built prompts from Coda
        """
        try:
            # Build API parameters from Coda's pre-built prompts
            api_params = {
                "model": request_data.model,
                "max_tokens": min(request_data.max_tokens, 8192),
                "temperature": max(0.0, min(1.0, request_data.temperature)),
                "messages": [
                    {
                        "role": "user", 
                        "content": self._inject_content_into_user_prompt(
                            request_data.user_prompt, 
                            chunk_content
                        )
                    }
                ]
            }
            
            # Add system prompt if provided by Coda
            if request_data.system_prompt:
                api_params["system"] = request_data.system_prompt
            
            # Extended thinking support
            if request_data.extended_thinking:
                # Add thinking parameter for Claude API
                api_params["thinking"] = {"type": "enabled"}
                
                # Set thinking budget if provided
                if request_data.thinking_budget:
                    api_params["thinking"]["budget_tokens"] = max(
                        1024, 
                        min(request_data.thinking_budget, request_data.max_tokens - 200)
                    )
                else:
                    api_params["thinking"]["budget_tokens"] = 2048  # Default thinking budget
                
                # Include thinking in response if requested
                if request_data.include_thinking:
                    api_params["include_thinking"] = True
                
                logger.info(f"Extended thinking enabled with budget: {api_params['thinking']['budget_tokens']}")
            
            logger.info(f"Calling Claude API with {len(chunk_content)} characters")
            start_time = time.time()
            
            # Use appropriate client call based on extended thinking
            if request_data.extended_thinking:
                # Use beta.messages.create for extended thinking
                response = self.client.beta.messages.create(
                    **api_params,
                    extra_headers={"anthropic-beta": "extended-thinking-2025-01-15"}
                )
            else:
                # Use regular messages.create for normal operation
                response = self.client.messages.create(**api_params)
            
            end_time = time.time()
            result = response.content[0].text
            
            logger.info(f"Claude API responded in {end_time - start_time:.2f}s, returned {len(result)} characters")
            
            return result
            
        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limit hit, will retry: {e}")
            raise
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Claude API call: {e}")
            raise
    
    def _inject_content_into_user_prompt(self, user_prompt: str, chunk_content: str) -> str:
        """
        Inject chunk content into Coda's pre-built user prompt
        
        Coda can use placeholders like {{CONTENT}} or {{CHUNK_CONTENT}} in their prompt
        """
        # Check for common placeholder patterns
        placeholders = [
            "{{CONTENT}}", 
            "{{CHUNK_CONTENT}}", 
            "{{ANALYSIS_CONTENT}}", 
            "{{DATA}}"
        ]
        
        for placeholder in placeholders:
            if placeholder in user_prompt:
                return user_prompt.replace(placeholder, chunk_content)
        
        # If no placeholder found, append content to end
        return f"{user_prompt}\n\n{chunk_content}"
    
    async def process_chunks_sequential(self, chunks: List[str], request_data: Any) -> List[str]:
        """Process multiple chunks sequentially to avoid rate limits"""
        results = []
        
        for i, chunk in enumerate(chunks):
            try:
                logger.info(f"Processing chunk {i+1}/{len(chunks)}")
                result = await self.process_chunk(chunk, request_data)
                results.append(result)
                
                # Add delay between chunks to respect rate limits
                if i < len(chunks) - 1:
                    await asyncio.sleep(2)
                    
            except Exception as e:
                logger.error(f"Chunk {i+1} failed: {e}")
                results.append(f"[Error processing chunk {i+1}: {str(e)[:200]}]")
        
        return results
    
    async def assess_quality(self, analysis_result: str) -> str:
        """Assess quality of analysis result using separate Claude call"""
        try:
            assessment_prompt = f"""Analyze this AI response and determine if it successfully completed the requested analysis. Respond with exactly one word: SUCCESS or FAILED.

SUCCESS = The response provides meaningful analysis, insights, or results relevant to the request.
FAILED = The response contains error messages, explicit refusals, or clearly states it cannot complete the task.

Response to analyze: {analysis_result[:1500]}"""

            response = self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=10,
                temperature=0.0,
                system="You are a quality checker. SUCCESS if the AI provided actual analysis, insights, or useful information. FAILED only for clear errors, explicit refusals like 'I cannot analyze', 'I don't see any content', or technical failures.",
                messages=[{"role": "user", "content": assessment_prompt}]
            )
            
            result = response.content[0].text.strip().upper()
            
            if result not in ["SUCCESS", "FAILED"]:
                logger.warning(f"Unexpected quality assessment result: {result}")
                return "SUCCESS"
            
            return result
            
        except Exception as e:
            logger.error(f"Quality assessment failed: {e}")
            return "SUCCESS"
    
    async def ensure_format_consistency(self, combined_result: str) -> str:
        """Ensure consistent formatting across all chunks"""
        try:
            consistency_prompt = f"""Review this analysis result and ensure ALL content follows the same formatting structure throughout. 
            
If you see content blocks using a specific format (like <content> tags), make sure ALL similar content uses that exact same format. Do not change the meaning or content, only ensure consistent formatting.
            
Analysis to format consistently:
{combined_result}"""
            
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=8192,
                temperature=0.1,
                messages=[{"role": "user", "content": consistency_prompt}]
            )
            
            return response.content[0].text.strip()
            
        except Exception as e:
            logger.error(f"Format consistency check failed: {e}")
            return combined_result  # Return original if consistency check fails
    
    async def generate_analysis_name(self, analysis_result: str) -> str:
        """Generate concise analysis name using Claude"""
        try:
            name_prompt = f"Generate a single professional title (5-7 words only, no extra text) for the following analysis: {analysis_result[:1500]}"
            
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=30,
                temperature=0.1,
                messages=[{"role": "user", "content": name_prompt}]
            )
            
            result = response.content[0].text.strip().strip('"\'.')
            
            if len(result) > 50:
                result = result[:50].strip()
            
            return result if result else "AI Analysis Result"
            
        except Exception as e:
            logger.error(f"Name generation failed: {e}")
            return "AI Analysis Result"
