# src/shared/config.py
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    claude_api_key: str = os.getenv("CLAUDE_API_KEY", "")
    queue_url: str = os.getenv("QUEUE_URL", "redis://localhost:6379")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    max_content_size: int = int(os.getenv("MAX_CONTENT_SIZE", "100000"))
    webhook_timeout: int = int(os.getenv("WEBHOOK_TIMEOUT", "30"))
    
    class Config:
        env_file = ".env"
        case_sensitive = False

_settings = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings