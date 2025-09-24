# src/shared/logging.py
import logging
import sys
from .config import get_settings

def setup_logging():
    settings = get_settings()
    
    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )
    
    # Set specific loggers
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Suppress HTTP request logs
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("redis").setLevel(logging.WARNING)