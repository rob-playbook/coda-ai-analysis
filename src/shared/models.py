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
    model: str = Field(default="claude-3-5-sonnet-20241022", description="Claude model to use")
    max_tokens: int = Field(default=2000, description="Maximum tokens")
    temperature: float = Field(default=0.2, description="Temperature setting")
    
    # EXTENDED THINKING SUPPORT
    extended_thinking: bool = Field(default=False, description="Enable extended thinking")
    thinking_budget: Optional[int] = Field(default=None, description="Thinking budget tokens")
    include_thinking: bool = Field(default=False, description="Include thinking in response")
    
    # ITERATION MODE
    is_iteration: bool = Field(default=False, description="Is this an iteration on existing analysis")
    
    # METADATA
    analysis_context: str = Field(..., description="Context identifier")
    webhook_url: str = Field(..., description="Coda webhook endpoint for results")
    template_config: Optional[Dict[str, Any]] = Field(default=None, description="Template metadata")
    project_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Project metadata")

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
