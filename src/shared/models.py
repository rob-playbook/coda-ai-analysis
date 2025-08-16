# src/shared/models.py
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
    prompt_config: Dict[str, Any] = Field(..., description="Complete prompt configuration")
    analysis_context: str = Field(..., description="Context identifier")
    webhook_url: str = Field(..., description="Coda webhook endpoint for results")
    
    # Iteration mode support
    is_iteration: bool = Field(default=False)
    iteration_content: Optional[str] = Field(default=None)
    
    # Template and metadata
    template_config: Optional[Dict[str, Any]] = Field(default=None)
    project_metadata: Optional[Dict[str, Any]] = Field(default=None)

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