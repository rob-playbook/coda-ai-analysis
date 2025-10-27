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
                
                # logger.info(f"Extended thinking enabled with budget: {api_params['thinking']['budget_tokens']}, temperature forced to 1.0")
                # NOTE: include_thinking is NOT sent to Claude API - it's used for post-processing
            else:
                # Use requested temperature for normal operation
                api_params["temperature"] = max(0.0, min(1.0, request_data.temperature))
            
            logger.info(f"Calling Claude API with {len(chunk_content)} characters using model: {request_data.model}")
            logger.info(f"User prompt length: {len(request_data.user_prompt)} characters")
            logger.info(f"System prompt length: {len(request_data.system_prompt) if request_data.system_prompt else 0} characters")
            start_time = time.time()
            
            # Add timeout protection to main API calls
            async with asyncio.timeout(300):  # 5-minute timeout for main analysis (increased for large content)
                # Use streaming for long requests to avoid 10-minute limit
                if request_data.max_tokens > 20000:  # Use streaming for large responses
                    logger.info("Using streaming for large response")
                    result_parts = []
                    
                    with self.client.messages.stream(**api_params) as stream:
                        for text in stream.text_stream:
                            result_parts.append(text)
                    
                    result = ''.join(result_parts)
                    response = stream.get_final_message()  # Get final message for metadata
                else:
                    # Use regular messages.create for smaller requests
                    response = self.client.messages.create(**api_params)
                    result = response.content[0].text
            
            end_time = time.time()
            logger.info(f"Claude API responded in {end_time - start_time:.2f}s")
            

            
            # REMOVED: Verbose content block logging for performance
            # for i, block in enumerate(response.content):
            #     block_type = getattr(block, 'type', 'unknown')
            #     if hasattr(block, 'text'):
            #         text_length = len(block.text)
            #         logger.info(f"Content block {i}: type={block_type}, length={text_length} chars")
            #         logger.info(f"Block {i} starts: {repr(block.text[:100])}")
            #         logger.info(f"Block {i} ends: {repr(block.text[-100:])}")
            #     else:
            #         logger.info(f"Content block {i}: type={block_type}, no text attribute")
            
            # Process response content based on response type and thinking settings
            if request_data.max_tokens > 20000:  # Streaming was used - result already extracted
                pass
            elif request_data.extended_thinking and not request_data.include_thinking:
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
                    for i, block in enumerate(response.content):
                        if hasattr(block, 'text'):
                            all_text.append(block.text)
                        elif hasattr(block, 'thinking'):
                            all_text.append(block.thinking)
                    result = "\n\n".join(all_text)
            
            # Check for potential truncation indicators
            if result.endswith(('00:', '<v ', 'So\n', '\n00:', '\n<v')):
                pass
            
            # Check if response seems incomplete
            if len(result) < len(chunk_content) * 0.5:  # If response is less than 50% of input
                pass
            
            return result
            
        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limit hit, will retry: {e}")
            raise
        except anthropic.APIError as e:
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
                if len(chunks) > 1:
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
        
        INTERACTIVE PROMPT DETECTION: Skips quality assessment for prompts that
        explicitly request user confirmation or interactive feedback, as these
        patterns are intentional rather than failures.
        
        TIMEOUT PROTECTION: Falls back to SUCCESS if quality assessment fails/times out.
        Main analysis should never fail due to quality assessment issues.
        """
        try:
            # PRE-CHECK: Does prompt explicitly request interactive feedback?
            interactive_patterns = [
                "ask me if I confirm",
                "confirm with yes or no",
                "Do you confirm",
                "ask me to confirm",
                "provide alternative interpretations"
            ]
            
            prompt_lower = request_data.user_prompt.lower()
            if any(pattern.lower() in prompt_lower for pattern in interactive_patterns):
                logger.info(f"Interactive prompt detected - bypassing quality assessment")
                return "SUCCESS"
            
            # Add timeout protection - quality assessment should not break main analysis
            async with asyncio.timeout(15):  # 15-second timeout for quality assessment
                # logger.info("Starting quality assessment using model: claude-sonnet-4-20250514")
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

4. TECHNICAL FAILURES: Are there error messages, processing failures, or empty responses?
   - Look for error codes, "processing failed", responses under 50 characters

EVALUATION RULES:
- If YES to any of questions 1-4: FAILED
- If NO to all questions 1-4: SUCCESS

You must respond with either SUCCESS or FAILED only.

Be strict: If the AI identifies that content doesn't match what was requested, that's FAILED regardless of any attempted workarounds."""

                response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=50,  # Allow enough tokens for reasoning
                    temperature=0.0,
                    system="You are an intelligent quality evaluator for automated workflows. Assess whether the AI response successfully fulfills the original request using semantic understanding, not pattern matching. Consider content alignment, completeness, and whether the deliverables match what was specifically asked for.",
                    messages=[{"role": "user", "content": assessment_prompt}]
                )
                
                result = response.content[0].text.strip().upper()
                
                # Extract just the first word (SUCCESS or FAILED) from response
                first_word = result.split()[0] if result.split() else result
                
                # DEBUG: Log Claude's full reasoning
                # logger.info(f"Quality assessment reasoning: {response.content[0].text}")
                # logger.info(f"Quality assessment input (first 500 chars): {analysis_result[:500]}")
                # logger.info(f"Quality assessment result: {first_word}")
                
                if first_word not in ["SUCCESS", "FAILED"]:
                    # logger.warning(f"Unexpected quality assessment result: {first_word}")
                    return "SUCCESS"  # Default to SUCCESS for unexpected responses
                
                return first_word
                
        except asyncio.TimeoutError:
            # logger.warning("Quality assessment timed out after 15 seconds - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check timeout
        except Exception as e:
            # logger.warning(f"Quality assessment failed: {e} - defaulting to SUCCESS")
            return "SUCCESS"  # Don't fail main analysis due to quality check errors
    
    async def ensure_format_consistency(self, combined_result: str, request_data: Any) -> str:
        """Ensure consistent formatting across all chunks with timeout protection"""
        try:
            # logger.info(f"Starting consistency check using model: {request_data.model}")
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
            # logger.warning("Format consistency check timed out - returning original result")
            return combined_result
        except Exception as e:
            # logger.warning(f"Format consistency check failed: {e} - returning original result")
            return combined_result  # Return original if consistency check fails
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_not_exception_type((asyncio.TimeoutError, anthropic.AuthenticationError)),
        reraise=True
    )
    async def process_files(self, files_data: List[Dict[str, any]], request_data: Any) -> str:
        """
        Process files through Claude API - PDFs as document blocks, others as extracted text
        FIXED: Now handles mixed content (files + regular text)
        """
        try:
            from src.worker.file_processor import FileProcessor
            file_processor = FileProcessor()
            
            # EXTRACT NON-FILE TEXT CONTENT from original content
            preserved_text = self._extract_non_file_content(request_data.content)
            
            # Clean the user prompt
            clean_prompt = request_data.user_prompt
            
            # Remove any FILE_URL references that might have leaked into the prompt
            if "FILE_URL:" in clean_prompt:
                lines = clean_prompt.split('\n')
                clean_lines = [line for line in lines if not line.strip().startswith('FILE_URL:')]
                clean_prompt = '\n'.join(clean_lines).strip()
                
                if not clean_prompt:
                    clean_prompt = "Please analyze the provided documents and summarize their key content."
            
            # Remove content placeholders (but keep actual content structure labels)
            content_placeholders = [
                "{{CONTENT}}", "{{CHUNK_CONTENT}}", "{{ANALYSIS_CONTENT}}", "{{DATA}}"
                # NOTE: Do NOT remove **SOURCE CONTENT:** and **TARGET CONTENT:** - these are actual content structure!
            ]
            
            for placeholder in content_placeholders:
                clean_prompt = clean_prompt.replace(placeholder, "").strip()
            
            # Remove URL references
            import re
            clean_prompt = re.sub(r'https://[^\s]+', '', clean_prompt)
            url_phrases = [
                "access the content at", "view the content at", "analyze the content at",
                "summarize the content at", "review the content at", "examine the content at",
                "at the provided URL", "from the URL", "in the URL", "the URL contains",
                "cannot access", "cannot view", "provided URL", "at this URL"
            ]
            for phrase in url_phrases:
                clean_prompt = re.sub(phrase, '', clean_prompt, flags=re.IGNORECASE)
            
            clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
            
            if len(clean_prompt.strip()) < 10:
                clean_prompt = "Please analyze the provided documents and summarize their key content."
            

            
            # **NEW LOGIC: Separate files by type**
            pdf_files = [f for f in files_data if f['mime_type'] == 'application/pdf']
            text_files = [f for f in files_data if f['mime_type'] != 'application/pdf' and 'image' not in f['mime_type']]
            image_files = [f for f in files_data if 'image' in f['mime_type']]
            

            
            # Start building content array
            content = [{
                "type": "text",
                "text": clean_prompt
            }]
            
            # Add PDFs as document blocks (supported)
            for i, pdf_file in enumerate(pdf_files):
                if 'base64_data' not in pdf_file:
                    raise Exception(f"PDF file {i+1} missing base64_data - download may have failed")
                
                doc_block = {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_file['base64_data']
                    }
                }
                content.append(doc_block)
            
            # Extract text from other files and add as regular text content
            if text_files:
                extracted_texts = []
                for i, text_file in enumerate(text_files):
                    try:
                        # Extract text content from file data
                        text_content = file_processor.extract_text_content(
                            text_file['data'], 
                            text_file['mime_type'], 
                            text_file['url']
                        )
                        
                        # Format the extracted content
                        file_header = f"=== File: {text_file['url'].split('/')[-1]} ({text_file['mime_type']}) ==="
                        extracted_texts.append(f"{file_header}\n{text_content}")
                        
                    except Exception as e:
                        logger.error(f"Failed to extract text from file {i+1}: {e}")
                        # Add error message instead of failing completely
                        error_msg = f"=== File: {text_file['url'].split('/')[-1]} ===\n[Error: Could not extract text content - {str(e)}]"
                        extracted_texts.append(error_msg)
                
                # Add all extracted text as a single text block
                if extracted_texts:
                    combined_text = "\n\n".join(extracted_texts)
                    content.append({
                        "type": "text",
                        "text": f"\n\nDocument Contents:\n{combined_text}"
                    })
            
            # ADD PRESERVED NON-FILE TEXT CONTENT with file replacement
            if preserved_text.strip():
                # Check if preserved text contains ANALYSIS CONTEXT section
                if "**ANALYSIS CONTEXT:**" in preserved_text:
                    # Reconstruct ANALYSIS CONTEXT with processed file content
                    reconstructed_text = self._reconstruct_analysis_context_with_files(preserved_text, files_data, file_processor)
                    content.append({
                        "type": "text",
                        "text": f"\n\n{reconstructed_text}"
                    })
                else:
                    content.append({
                        "type": "text",
                        "text": f"\n\nAdditional Content:\n{preserved_text}"
                    })
            
            # Add images as image blocks (if any)
            for i, image_file in enumerate(image_files):
                if 'base64_data' not in image_file:
                    logger.warning(f"Image file {i+1} missing base64_data - skipping")
                    continue
                
                image_block = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_file['mime_type'],
                        "data": image_file['base64_data']
                    }
                }
                content.append(image_block)
            

            
            # Build API parameters
            api_params = {
                "model": request_data.model,
                "max_tokens": request_data.max_tokens,
                "messages": [{
                    "role": "user",
                    "content": content
                }]
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
                api_params["temperature"] = 1.0  # Required for thinking
            else:
                api_params["temperature"] = max(0.0, min(1.0, request_data.temperature))
            

            
            # Log character counts for file processing
            total_content_chars = sum(len(str(block.get('text', ''))) for block in content if block.get('type') == 'text')
            logger.info(f"Calling Claude API for file processing with {len(files_data)} files using model: {request_data.model}")
            logger.info(f"User prompt length: {len(clean_prompt)} characters")
            logger.info(f"System prompt length: {len(request_data.system_prompt) if request_data.system_prompt else 0} characters")
            logger.info(f"Total text content: {total_content_chars} characters")
            
            start_time = time.time()
            
            # Files require longer timeout due to processing overhead
            async with asyncio.timeout(300):  # 5-minute timeout for file processing
                # Use streaming for large responses
                if request_data.max_tokens > 20000:
                    logger.info("Using streaming for large file response")
                    result_parts = []
                    
                    with self.client.messages.stream(**api_params) as stream:
                        for text in stream.text_stream:
                            result_parts.append(text)
                    
                    result = ''.join(result_parts)
                    response = stream.get_final_message()
                else:
                    response = self.client.messages.create(**api_params)
                    result = response.content[0].text
            
            end_time = time.time()
            logger.info(f"Claude API responded in {end_time - start_time:.2f}s for file processing")
            
            # Process response content based on thinking settings
            if request_data.max_tokens > 20000:
                # Streaming - result already extracted
                pass
            elif request_data.extended_thinking and not request_data.include_thinking:
                # Strip thinking blocks, keep only text blocks
                text_blocks = [block.text for block in response.content if block.type == "text"]
                result = "\n\n".join(text_blocks) if text_blocks else ""
            else:
                # Standard processing
                if len(response.content) == 1:
                    result = response.content[0].text
                else:
                    all_text = []
                    for block in response.content:
                        if hasattr(block, 'text'):
                            all_text.append(block.text)
                        elif hasattr(block, 'thinking'):
                            all_text.append(block.thinking)
                    result = "\n\n".join(all_text)
            

            
            return result
            
        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limit hit during file processing, will retry: {e}")
            raise
        except anthropic.APIError as e:
            logger.error(f"Claude API error during file processing: {e}")
            raise
        except asyncio.TimeoutError as e:
            logger.error(f"Claude API file processing timed out after 15 minutes: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Claude file processing: {e}")
            raise

    async def generate_analysis_name(self, analysis_result: str, request_data: Any) -> str:
        """Generate concise analysis name using Claude with timeout protection based on request context"""
        try:
            # Quick check - if this looks like an error, don't waste API call
            if (analysis_result.startswith("[Error processing") or 
                "Error code:" in analysis_result[:200] or 
                len(analysis_result.strip()) < 50):
                return "Processing Error"
            
            # logger.info("Starting name generation using model: claude-sonnet-4-20250514")
            
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
                    model="claude-sonnet-4-20250514",
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
            # logger.warning("Name generation timed out - using default name")
            return "AI Analysis Result"
    
    def _reconstruct_analysis_context_with_files(self, preserved_text: str, files_data: List[Dict[str, any]], file_processor) -> str:
        """
        Reconstruct ANALYSIS CONTEXT section, replacing FILE_URLs with processed file content
        """
        try:
            lines = preserved_text.split('\n')
            reconstructed_lines = []
            in_analysis_context = False
            
            for line in lines:
                line_stripped = line.strip()
                
                if line_stripped == "**ANALYSIS CONTEXT:**":
                    in_analysis_context = True
                    reconstructed_lines.append(line)
                elif line_stripped.startswith("**") and line_stripped.endswith(":**"):
                    in_analysis_context = False
                    reconstructed_lines.append(line)
                elif in_analysis_context and "FILE_URL:" in line:
                    # Find the corresponding processed file content
                    file_url_start = line.find('FILE_URL:')
                    if file_url_start > 0:
                        label_part = line[:file_url_start].rstrip(' :,-')
                        url_part = line[file_url_start + 9:].strip()  # Remove FILE_URL: prefix
                        
                        # Find matching file in processed files_data
                        file_content = "[File content not found]"
                        for file_data in files_data:
                            if url_part in file_data.get('url', ''):
                                if file_data['mime_type'] == 'application/pdf':
                                    file_content = "[PDF content processed as document block]"
                                else:
                                    try:
                                        file_content = file_processor.extract_text_content(
                                            file_data['data'], 
                                            file_data['mime_type'], 
                                            file_data['url']
                                        )
                                        # Truncate very long content
                                        if len(file_content) > 500:
                                            file_content = file_content[:500] + "..."
                                    except Exception as e:
                                        file_content = f"[Error extracting file content: {str(e)}]"
                                break
                        
                        reconstructed_lines.append(f"{label_part}: {file_content}")
                        logger.info(f"Replaced FILE_URL with content for: {label_part.strip()}")
                    else:
                        reconstructed_lines.append(line)  # Keep as is if parsing fails
                else:
                    reconstructed_lines.append(line)
            
            return '\n'.join(reconstructed_lines)
            
        except Exception as e:
            logger.error(f"Error reconstructing ANALYSIS CONTEXT: {e}")
            return preserved_text  # Return original on error
    
    def _extract_non_file_content(self, original_content: str) -> str:
        """
        Extract non-FILE_URL text content from mixed content
        This preserves regular text while removing FILE_URL entries
        FIXED: Preserves ANALYSIS CONTEXT structure labels
        """
        try:
            # Split content by lines to process line by line
            lines = original_content.split('\n')
            preserved_lines = []
            
            in_analysis_context = False
            
            for line in lines:
                line_stripped = line.strip()
                
                # Track which section we're in
                if line_stripped == "**ANALYSIS CONTEXT:**":
                    in_analysis_context = True
                    preserved_lines.append(line)
                    continue
                elif line_stripped.startswith("**") and line_stripped.endswith(":**"):
                    # Entering a different section
                    in_analysis_context = False
                    preserved_lines.append(line)
                    continue
                
                # Handle FILE_URL lines based on section
                if 'FILE_URL:' in line_stripped:
                    if in_analysis_context:
                        # In ANALYSIS CONTEXT: preserve the context label part only
                        if 'FILE_URL:' in line:
                            file_url_index = line.find('FILE_URL:')
                            if file_url_index > 0:
                                # Keep everything before FILE_URL
                                label_part = line[:file_url_index].rstrip(' :,-')
                                if label_part.strip():
                                    preserved_lines.append(label_part)
                                    logger.info(f"Preserved ANALYSIS CONTEXT label: {label_part.strip()}")
                            else:
                                logger.info(f"Filtering out pure FILE_URL line in context: {line_stripped[:100]}...")
                        else:
                            logger.info(f"Filtering out FILE_URL line: {line_stripped[:100]}...")
                    else:
                        # In other sections: remove entire FILE_URL line (current behavior)
                        logger.info(f"Filtering out FILE_URL line: {line_stripped[:100]}...")
                else:
                    # No FILE_URL: keep the line as is
                    preserved_lines.append(line)
            
            preserved_content = '\n'.join(preserved_lines).strip()
            logger.info(f"Preserved {len(preserved_content)} characters of non-file content")
            
            return preserved_content
            
        except Exception as e:
            logger.error(f"Error extracting non-file content: {e}")
            return ""  # Return empty string on error, don't fail the whole request