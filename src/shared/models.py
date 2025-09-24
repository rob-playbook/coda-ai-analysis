# Updated models.py - Coda sends pre-built prompts
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"

class AnalysisRequest(BaseModel):
    record_id: str = Field(..., description="Coda record ID for tracking")
    content: str = Field(..., description="Content to analyze")
    
    # PRE-BUILT PROMPTS FROM CODA
    system_prompt: Optional[str] = Field(default=None, description="Complete system prompt built by Coda")
    user_prompt: str = Field(..., description="Complete user prompt built by Coda")
    
    # API CONFIGURATION
    model: str = Field(default="claude-sonnet-4-20250514", description="Claude model to use")
    max_tokens: int = Field(default=2000, description="Maximum tokens")
    temperature: float = Field(default=0.2, description="Temperature setting")
    
    # EXTENDED THINKING SUPPORT
    extended_thinking: bool = Field(default=False, description="Enable extended thinking")
    thinking_budget: Optional[int] = Field(default=None, description="Thinking budget tokens")
    include_thinking: bool = Field(default=False, description="Include thinking in response")
    
    # ESSENTIAL METADATA
    webhook_url: str = Field(..., description="Coda webhook endpoint for results")
    template_config: Optional[Dict[str, Any]] = Field(default=None, description="Template metadata")
    project_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Project metadata")

class PollingRequest(BaseModel):
    """Request model for polling endpoints (no webhook required)"""
    record_id: str = Field(..., description="Coda record ID for tracking")
    
    # SPLIT CONTENT FIELDS (replaces single 'content' field)
    source1: str = Field(..., description="Source content part 1")
    source2: Optional[str] = Field(default=None, description="Source content part 2")
    source3: Optional[str] = Field(default=None, description="Source content part 3")
    source4: Optional[str] = Field(default=None, description="Source content part 4")
    source5: Optional[str] = Field(default=None, description="Source content part 5")
    source6: Optional[str] = Field(default=None, description="Source content part 6")
    target1: Optional[str] = Field(default=None, description="Target content part 1")
    target2: Optional[str] = Field(default=None, description="Target content part 2")
    target3: Optional[str] = Field(default=None, description="Target content part 3")
    target4: Optional[str] = Field(default=None, description="Target content part 4")
    target5: Optional[str] = Field(default=None, description="Target content part 5")
    target6: Optional[str] = Field(default=None, description="Target content part 6")
    context1: Optional[str] = Field(default=None, description="Analysis context part 1")
    context2: Optional[str] = Field(default=None, description="Analysis context part 2")
    context3: Optional[str] = Field(default=None, description="Analysis context part 3")
    context4: Optional[str] = Field(default=None, description="Analysis context part 4")
    context5: Optional[str] = Field(default=None, description="Analysis context part 5")
    context6: Optional[str] = Field(default=None, description="Analysis context part 6")
    
    # PRE-BUILT PROMPTS FROM CODA
    system_prompt: Optional[str] = Field(default=None, description="Complete system prompt built by Coda")
    user_prompt: str = Field(..., description="Complete user prompt built by Coda")
    
    # API CONFIGURATION
    model: str = Field(default="claude-sonnet-4-20250514", description="Claude model to use")
    max_tokens: int = Field(default=2000, description="Maximum tokens")
    temperature: float = Field(default=0.2, description="Temperature setting")
    
    # EXTENDED THINKING SUPPORT
    extended_thinking: bool = Field(default=False, description="Enable extended thinking")
    thinking_budget: Optional[int] = Field(default=None, description="Thinking budget tokens")
    include_thinking: bool = Field(default=False, description="Include thinking in response")
    
    # OPTIONAL METADATA
    template_config: Optional[Dict[str, Any]] = Field(default=None, description="Template metadata")
    project_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Project metadata")
    
    def reconstruct_content(self) -> str:
        """Reconstruct content from split pieces"""
        # Reconstruct source content
        source_parts = [self.source1 or '']
        source_parts.extend([part or '' for part in [self.source2, self.source3, self.source4, self.source5, self.source6]])
        full_source = ''.join(source_parts)
        
        # Reconstruct target content
        target_parts = [part or '' for part in [self.target1, self.target2, self.target3, self.target4, self.target5, self.target6]]
        full_target = ''.join(target_parts)
        
        # Reconstruct context content
        context_parts = [part or '' for part in [self.context1, self.context2, self.context3, self.context4, self.context5, self.context6]]
        full_context = ''.join(context_parts)
        
        # Build final content structure
        content_sections = []
        
        if full_target:
            content_sections.append(f"**TARGET CONTENT:**\n{full_target}")
        
        content_sections.append(f"**SOURCE CONTENT:**\n{full_source}")
        
        if full_context:
            content_sections.append(f"**ANALYSIS CONTEXT:**\n{full_context}")
        
        return '\n\n'.join(content_sections)
    
    def to_analysis_request(self) -> 'AnalysisRequest':
        """Convert to AnalysisRequest for background processing"""
        return AnalysisRequest(
            record_id=self.record_id,
            content=self.reconstruct_content(),  # Use reconstructed content
            system_prompt=self.system_prompt,
            user_prompt=self.user_prompt,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            extended_thinking=self.extended_thinking,
            thinking_budget=self.thinking_budget,
            include_thinking=self.include_thinking,
            webhook_url="",  # Not used for polling
            template_config=self.template_config,
            project_metadata=self.project_metadata
        )

class AnalysisJob(BaseModel):
    job_id: str
    record_id: str
    status: JobStatus
    request_data: AnalysisRequest
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2

class AnalysisResult(BaseModel):
    record_id: str
    status: str  # "SUCCESS" or "FAILED"
    analysis_result: Optional[str] = None
    analysis_name: Optional[str] = None
    error_message: Optional[str] = None
    processing_stats: Dict[str, Any]
