# src/worker/claude.py
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
    async def process_chunk(self, chunk_content: str, prompt_config: Dict[str, Any]) -> str:
        """Process single chunk through Claude API with retry logic"""
        try:
            # Build complete prompt from configuration
            system_prompt = self._build_system_prompt(prompt_config)
            user_prompt = self._build_user_prompt(chunk_content, prompt_config)
            
            # Configure API call parameters
            api_params = {
                "model": prompt_config.get("model", "claude-3-5-sonnet-20241022"),
                "max_tokens": min(prompt_config.get("max_tokens", 2000), 8192),
                "temperature": max(0.0, min(1.0, prompt_config.get("temperature", 0.2))),
                "messages": [{"role": "user", "content": user_prompt}]
            }
            
            if system_prompt:
                api_params["system"] = system_prompt
            
            logger.info(f"Calling Claude API with {len(chunk_content)} characters")
            start_time = time.time()
            
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
    
    async def process_chunks_sequential(self, chunks: List[str], prompt_config: Dict[str, Any]) -> List[str]:
        """Process multiple chunks sequentially to avoid rate limits"""
        results = []
        
        for i, chunk in enumerate(chunks):
            try:
                logger.info(f"Processing chunk {i+1}/{len(chunks)}")
                result = await self.process_chunk(chunk, prompt_config)
                results.append(result)
                
                # Add delay between chunks to respect rate limits
                if i < len(chunks) - 1:
                    await asyncio.sleep(2)
                    
            except Exception as e:
                logger.error(f"Chunk {i+1} failed: {e}")
                results.append(f"[Error processing chunk {i+1}: {str(e)[:200]}]")
        
        return results
    
    def _build_system_prompt(self, prompt_config: Dict[str, Any]) -> str:
        """Build system prompt from configuration"""
        if not prompt_config.get("include_system_prompt", True):
            return ""
        
        language = prompt_config.get("language", "English US")
        components = [f"Create output in {language} (language and spelling)."]
        
        # Add role, approach, and speed/accuracy if specified
        for key in ["analyst_role", "system_approach", "system_speed_accuracy"]:
            value = prompt_config.get(key)
            if value and str(value).strip():
                components.append(str(value).strip())
        
        return " ".join(components)
    
    def _build_user_prompt(self, chunk_content: str, prompt_config: Dict[str, Any]) -> str:
        """Build user prompt from chunk content and configuration"""
        prompt_parts = []
        
        # Handle free-text vs structured prompts
        free_prompt = prompt_config.get("free_prompt")
        if free_prompt and str(free_prompt).strip():
            prompt_parts.append(str(free_prompt).strip())
            
            # Optionally include main prompt components
            if prompt_config.get("include_main_prompt", False):
                main_components = self._build_main_prompt_components(prompt_config)
                if main_components:
                    prompt_parts.append(main_components)
        else:
            # Structured mode - build from components
            main_components = self._build_main_prompt_components(prompt_config)
            if main_components:
                prompt_parts.append(main_components)
        
        # Add content if enabled
        if prompt_config.get("include_content", True):
            prompt_parts.append(chunk_content)
        
        return "\n\n".join(prompt_parts)
    
    def _build_main_prompt_components(self, prompt_config: Dict[str, Any]) -> str:
        """Build main prompt from individual components"""
        components = []
        
        # Action + Focus
        action = str(prompt_config.get("main_action", "")).strip()
        focus = str(prompt_config.get("main_focus", "")).strip()
        if action or focus:
            action_focus = f"{action} {focus}".strip()
            if action_focus:
                components.append(action_focus)
        
        # Scope
        scope = str(prompt_config.get("main_scope", "")).strip()
        if scope:
            components.append(f"Focus on {scope}")
        
        # Output
        output = str(prompt_config.get("main_output", "")).strip()
        if output:
            # Handle [NUMBER] placeholder
            output_number = prompt_config.get("main_output_number", "")
            if output_number and "[NUMBER]" in output:
                output = output.replace("[NUMBER]", str(output_number))
            components.append(f"Create {output}")
        
        # Constraints
        constraint = str(prompt_config.get("main_output_constraint", "")).strip()
        if constraint:
            components.append(constraint)
        
        # Additional instructions
        additional = str(prompt_config.get("main_additional_text", "")).strip()
        if additional:
            components.append(f"Additional instructions: {additional}")
        
        return ". ".join(components) + "." if components else ""
    
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