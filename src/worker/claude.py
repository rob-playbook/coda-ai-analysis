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
                thinking_budget = request_data.thinking_budget or 2048
                api_params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": max(1024, min(thinking_budget, request_data.max_tokens - 200))
                }
                
                # IMPORTANT: Claude API requires temperature=1 when thinking is enabled
                api_params["temperature"] = 1.0
                
                logger.info(f"Extended thinking enabled with budget: {api_params['thinking']['budget_tokens']}, temperature forced to 1.0")
                # NOTE: include_thinking is NOT sent to Claude API - it's used for post-processing
            else:
                # Use requested temperature for normal operation
                api_params["temperature"] = max(0.0, min(1.0, request_data.temperature))
            
            logger.info(f"Calling Claude API with {len(chunk_content)} characters using model: {request_data.model}")
            start_time = time.time()
            
            # Use regular messages.create for all requests (SDK 0.64+ supports thinking parameter)
            response = self.client.messages.create(**api_params)
            
            end_time = time.time()
            
            # Process response content based on include_thinking flag
            if request_data.extended_thinking and not request_data.include_thinking:
                # Strip thinking blocks, keep only text blocks
                text_blocks = [block.text for block in response.content if block.type == "text"]
                result = "\n\n".join(text_blocks) if text_blocks else ""
            else:
                # Include everything (default behavior) - get all content as text
                if len(response.content) == 1:
                    # Single content block (normal case)
                    result = response.content[0].text
                else:
                    # Multiple content blocks - join all text content
                    all_text = []
                    for block in response.content:
                        if hasattr(block, 'text'):
                            all_text.append(block.text)
                        elif hasattr(block, 'thinking'):
                            all_text.append(block.thinking)
                    result = "\n\n".join(all_text)
            
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
        """Assess quality of analysis result using separate Claude call
        
        Enhanced to catch "helpful but not actionable" responses as failures.
        Business requirement: Must deliver analysis, not ask for clarification.
        
        Uses explicit pattern matching for common failure phrases like:
        - "I cannot provide the requested analysis"
        - "doesn't match what I expected" 
        - "Would you like me to:"
        - "Since this content doesn't align with..."
        
        These responses break automated workflows even though they're "helpful".
        
        TIMEOUT PROTECTION: Falls back to SUCCESS if quality assessment fails/times out.
        Main analysis should never fail due to quality assessment issues.
        """
        try:
            # Add timeout protection - quality assessment should not break main analysis
            async with asyncio.timeout(15):  # 15-second timeout for quality assessment
                logger.info("Starting quality assessment using model: claude-3-haiku-20240307")
                assessment_prompt = f"""Analyze this AI response and determine if it successfully completed the requested analysis. Respond with exactly one word: SUCCESS or FAILED.

AUTOMATIC FAILED phrases (if any of these appear, mark FAILED):
- "I cannot provide the requested analysis"
- "I cannot provide the"
- "I cannot properly"
- "I cannot enhance"
- "I cannot create"
- "Given this mismatch, I cannot"
- "doesn't match what I expected"
- "doesn't align with"
- "there's a complete mismatch"
- "there's a mismatch"
- "appears to be a mismatch"
- "Would you like me to:"
- "Would you like me to"
- "Please advise on how"
- "Please advise"
- "Wait for you to provide"
- "appears to be about X rather than Y"
- "seems to be a mismatch"
- "There seems to be"
- "Since this content doesn't"
- "these are completely different subjects"
- "no overlap"
- "completely different"
- "significant mismatch"
- "1)"
- "2)"
- "3)"

Other FAILED indicators:
- Contains error messages or error codes
- Explicit refusals ("I cannot analyze", "I'm unable to")
- Technical failure messages
- Empty or nonsensical content
- Very short responses that don't address the request
- Identifies content problems instead of analyzing the provided content
- Offers multiple choice options instead of delivering analysis
- Points out discrepancies and stops there

SUCCESS indicators:
- Provides actual analysis, insights, or structured results using whatever content was provided
- Delivers findings and conclusions regardless of perceived content issues
- Contains substantive analytical content that answers the original request
- Proceeds with analysis using the source material provided

Response to analyze: {analysis_result[:1500]}"""

                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=10,
                    temperature=0.0,
                    system="You are a strict quality checker for automated workflows. IMMEDIATELY mark FAILED if you see ANY of these exact phrases: 'I cannot properly', 'Given this mismatch', 'there's a mismatch', 'Would you like me to', 'Please advise', '1)', '2)', '3)', 'completely different subjects'. These responses break workflows even if they seem helpful. SUCCESS only means actual analysis was delivered.",
                    messages=[{"role": "user", "content": assessment_prompt}]
                )
                
                result = response.content[0].text.strip().upper()
                
                if result not in ["SUCCESS", "FAILED"]:
                    logger.warning(f"Unexpected quality assessment result: {result}")
                    return "SUCCESS"  # Default to SUCCESS for unexpected responses
                
                logger.info(f"Quality assessment completed: {result}")
                return result
                
        except asyncio.TimeoutError:
            logger.warning("Quality assessment timed out after 15 seconds - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check timeout
        except Exception as e:
            logger.warning(f"Quality assessment failed: {e} - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check errors
    
    async def ensure_format_consistency(self, combined_result: str, request_data: Any) -> str:
        """Ensure consistent formatting across all chunks"""
        try:
            logger.info(f"Starting consistency check using model: {request_data.model}")
            consistency_prompt = f"""You previously processed this request in chunks. Here was the original prompt:
{request_data.user_prompt}

Now rewrite this entire analysis with consistent formatting throughout, following the original requirements. Return the COMPLETE analysis with every single piece of content.

Do not add, remove, or modify any analysis content - only fix formatting inconsistencies.

Return the full reformatted analysis:
{combined_result}"""
            
            response = self.client.messages.create(
                model=request_data.model,
                max_tokens=min(request_data.max_tokens, 8192),
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
            # Quick check - if this looks like an error, don't waste API call
            if (analysis_result.startswith("[Error processing") or 
                "Error code:" in analysis_result[:200] or 
                len(analysis_result.strip()) < 50):
                return "Processing Error"
            
            logger.info("Starting name generation using model: claude-3-haiku-20240307")
            name_prompt = f"Generate a single professional title (5-7 words only, no extra text) for the following analysis: {analysis_result[:1500]}"
            
            response = self.client.messages.create(
                model="claude-3-haiku-20240307",
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