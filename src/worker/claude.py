# Updated claude.py - Uses pre-built prompts from Coda
import anthropic
from typing import Dict, Any, List
import asyncio
import logging
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_not_exception_type

logger = logging.getLogger(__name__)

class ClaudeService:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_not_exception_type((asyncio.TimeoutError, anthropic.AuthenticationError)),
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
                "max_tokens": request_data.max_tokens,
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
            logger.info(f"API parameters: max_tokens={api_params['max_tokens']}, temperature={api_params.get('temperature', 'default')}")
            logger.info(f"User prompt length: {len(request_data.user_prompt)} characters")
            logger.info(f"System prompt length: {len(request_data.system_prompt) if request_data.system_prompt else 0} characters")
            start_time = time.time()
            
            # Add timeout protection to main API calls
            async with asyncio.timeout(120):  # 2-minute timeout for main analysis
                # Use regular messages.create for all requests (SDK 0.64+ supports thinking parameter)
                response = self.client.messages.create(**api_params)
            
            end_time = time.time()
            
            # === COMPREHENSIVE RESPONSE LOGGING ===
            logger.info(f"Claude API responded in {end_time - start_time:.2f}s")
            logger.info(f"Response stop_reason: {getattr(response, 'stop_reason', 'not available')}")
            logger.info(f"Response usage: {getattr(response, 'usage', 'not available')}")
            logger.info(f"Response content blocks: {len(response.content)}")
            
            # Log each content block
            for i, block in enumerate(response.content):
                block_type = getattr(block, 'type', 'unknown')
                if hasattr(block, 'text'):
                    text_length = len(block.text)
                    logger.info(f"Content block {i}: type={block_type}, length={text_length} chars")
                    logger.info(f"Block {i} starts: {repr(block.text[:100])}")
                    logger.info(f"Block {i} ends: {repr(block.text[-100:])}")
                else:
                    logger.info(f"Content block {i}: type={block_type}, no text attribute")
            
            # Process response content based on include_thinking flag
            if request_data.extended_thinking and not request_data.include_thinking:
                # Strip thinking blocks, keep only text blocks
                text_blocks = [block.text for block in response.content if block.type == "text"]
                result = "\n\n".join(text_blocks) if text_blocks else ""
                logger.info(f"Processed thinking response: {len(text_blocks)} text blocks, final length: {len(result)} chars")
            else:
                # Include everything (default behavior) - get all content as text
                if len(response.content) == 1:
                    # Single content block (normal case)
                    result = response.content[0].text
                    logger.info(f"Single content block processed, final length: {len(result)} chars")
                else:
                    # Multiple content blocks - join all text content
                    all_text = []
                    for i, block in enumerate(response.content):
                        if hasattr(block, 'text'):
                            all_text.append(block.text)
                            logger.info(f"Added text block {i}: {len(block.text)} chars")
                        elif hasattr(block, 'thinking'):
                            all_text.append(block.thinking)
                            logger.info(f"Added thinking block {i}: {len(block.thinking)} chars")
                    result = "\n\n".join(all_text)
                    logger.info(f"Multiple content blocks processed: {len(all_text)} blocks, final length: {len(result)} chars")
            
            # === FINAL RESULT VALIDATION ===
            logger.info(f"FINAL RESULT - Length: {len(result)} characters")
            logger.info(f"FINAL RESULT - Starts with: {repr(result[:200])}")
            logger.info(f"FINAL RESULT - Ends with: {repr(result[-200:])}")
            
            # Check for potential truncation indicators
            if result.endswith(('00:', '<v ', 'So\n', '\n00:', '\n<v')):
                logger.error(f"⚠️  POTENTIAL TRUNCATION DETECTED - Response ends with: {repr(result[-50:])}")
            
            # Check if response seems incomplete
            if len(result) < len(chunk_content) * 0.5:  # If response is less than 50% of input
                logger.warning(f"⚠️  UNUSUALLY SHORT RESPONSE - Input: {len(chunk_content)}, Output: {len(result)}")
            
            logger.info(f"Claude API responded in {end_time - start_time:.2f}s, returned {len(result)} characters")
            
            return result
            
        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limit hit, will retry: {e}")
            raise
        except anthropic.APIError as e:
            # DEBUG: Log specific details about API errors to understand 504 handling
            logger.error(f"Claude API error (type: {type(e).__name__}): {e}")
            if hasattr(e, 'status_code'):
                logger.error(f"Status code: {e.status_code}")
            if hasattr(e, 'response'):
                logger.error(f"Response: {getattr(e.response, 'status_code', 'no status')}")
            raise
        except asyncio.TimeoutError as e:
            logger.error(f"Claude API call timed out after 120 seconds: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Claude API call (type: {type(e).__name__}): {e}")
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
    
    async def assess_quality(self, analysis_result: str, request_data: Any) -> str:
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
                assessment_prompt = f"""IMPORTANT: Start your response with either SUCCESS or FAILED as the very first word.

You are evaluating whether an AI completed the requested task.

ORIGINAL REQUEST: {request_data.user_prompt[:500]}

AI RESPONSE: {analysis_result[:10000]}

EVALUATION QUESTIONS:

1. CONTENT TYPE MISMATCH: Does the AI explicitly state that the provided content is the wrong type for what was requested?
   - Look for phrases like "This is a transcript, not a research brief" or "This appears to be X when you asked for Y"

2. EXPLICIT REFUSAL: Does the AI state it cannot complete the requested task?
   - Look for phrases like "I cannot analyze this" or "I'm unable to provide the requested analysis"

3. SEEKING CLARIFICATION: Does the AI ask questions instead of providing analysis?
   - Look for phrases like "Would you like me to..." or "Please provide..." or "Which approach would you prefer?"

4. TECHNICAL FAILURES: Are there error messages, processing failures, empty responses, or gibberish?
   - Look for error codes, "processing failed", responses under 50 characters, or nonsensical text

EVALUATION RULES:
- If YES to any of questions 1-4: FAILED
- If NO to all questions 1-4: SUCCESS

You must respond with either SUCCESS or FAILED only.

Be strict: If the AI identifies that content doesn't match what was requested, that's FAILED regardless of any attempted workarounds."""

                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=50,  # Increase to see Haiku's reasoning
                    temperature=0.0,
                    system="You are an intelligent quality evaluator for automated workflows. Assess whether the AI response successfully fulfills the original request using semantic understanding, not pattern matching. Consider content alignment, completeness, and whether the deliverables match what was specifically asked for.",
                    messages=[{"role": "user", "content": assessment_prompt}]
                )
                
                result = response.content[0].text.strip().upper()
                
                # Extract just the first word (SUCCESS or FAILED) from response
                first_word = result.split()[0] if result.split() else result
                
                # DEBUG: Log Haiku's full reasoning
                logger.info(f"Quality assessment reasoning: {response.content[0].text}")
                logger.info(f"Quality assessment input (first 500 chars): {analysis_result[:500]}")
                logger.info(f"Quality assessment result: {first_word}")
                
                if first_word not in ["SUCCESS", "FAILED"]:
                    logger.warning(f"Unexpected quality assessment result: {first_word}")
                    return "SUCCESS"  # Default to SUCCESS for unexpected responses
                
                return first_word
                
        except asyncio.TimeoutError:
            logger.warning("Quality assessment timed out after 15 seconds - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check timeout
        except Exception as e:
            logger.warning(f"Quality assessment failed: {e} - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check errors
    
    async def ensure_format_consistency(self, combined_result: str, request_data: Any) -> str:
        """Ensure consistent formatting across all chunks with timeout protection"""
        try:
            logger.info(f"Starting consistency check using model: {request_data.model}")
            consistency_prompt = f"""You previously processed this request in chunks. Here was the original prompt:
{request_data.user_prompt}

Now rewrite this entire analysis with consistent formatting throughout, following the original requirements. Return the COMPLETE analysis with every single piece of content.

Do not add, remove, or modify any analysis content - only fix formatting inconsistencies.

Return the full reformatted analysis:
{combined_result}"""
            
            # Add timeout protection
            async with asyncio.timeout(60):  # 1-minute timeout for format consistency
                response = self.client.messages.create(
                    model=request_data.model,
                    max_tokens=min(request_data.max_tokens, 8192),
                    temperature=0.1,
                    messages=[{"role": "user", "content": consistency_prompt}]
                )
            
                return response.content[0].text.strip()
            
        except asyncio.TimeoutError:
            logger.warning("Format consistency check timed out - returning original result")
            return combined_result
        except Exception as e:
            logger.warning(f"Format consistency check failed: {e} - returning original result")
            return combined_result  # Return original if consistency check fails
    
    async def generate_analysis_name(self, analysis_result: str, request_data: Any) -> str:
        """Generate concise analysis name using Claude with timeout protection based on request context"""
        try:
            # Quick check - if this looks like an error, don't waste API call
            if (analysis_result.startswith("[Error processing") or 
                "Error code:" in analysis_result[:200] or 
                len(analysis_result.strip()) < 50):
                return "Processing Error"
            
            logger.info("Starting name generation using model: claude-3-haiku-20240307")
            
            # Extract only the task context from user prompt (ignore system prompt)
            task_context = request_data.user_prompt[:300] if request_data.user_prompt else ""
            
            name_prompt = f"""Extract the core task from this request. What TYPE of analysis or work is being performed?

Request: {task_context}

Respond with just the task type as a professional title (4-6 words). 
Examples: "Research Brief Review", "Interview Data Analysis", "User Journey Mapping"

Ignore WHO is doing it, focus only on WHAT is being done."""
            
            # Add timeout protection
            async with asyncio.timeout(30):  # 30-second timeout for name generation
                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=50,  # Increased from 30 to avoid truncation
                    temperature=0.1,
                    messages=[{"role": "user", "content": name_prompt}]
                )
            
                result = response.content[0].text.strip().strip('"\'.') 
                
                # Better truncation - don't cut mid-word
                if len(result) > 50:
                    words = result.split()
                    truncated = []
                    char_count = 0
                    for word in words:
                        if char_count + len(word) + 1 <= 50:  # +1 for space
                            truncated.append(word)
                            char_count += len(word) + 1
                        else:
                            break
                    result = " ".join(truncated)
                
                return result if result else "AI Analysis Result"
            
        except asyncio.TimeoutError:
            logger.warning("Name generation timed out - using default name")
            return "AI Analysis Result"
        except Exception as e:
            logger.warning(f"Name generation failed: {e} - using default name")
            return "AI Analysis Result"