# src/worker/file_processor.py
import aiohttp
import asyncio
import base64
import logging
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse
import mimetypes
from io import BytesIO

logger = logging.getLogger(__name__)

class FileProcessor:
    def __init__(self, max_file_size: int = 30 * 1024 * 1024):  # 30MB default
        self.max_file_size = max_file_size
        self.supported_mime_types = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.csv': 'text/csv',
            '.vtt': 'text/vtt',
            '.json': 'application/json',
            '.jpeg': 'image/jpeg',
            '.jpg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
    
    def extract_file_urls(self, content: str) -> List[str]:
        """Extract FILE_URL entries from content string"""
        try:

            
            # Handle both direct FILE_URL and wrapped content
            if not (content.startswith("FILE_URL:") or "FILE_URL:" in content):

                return []
            
            # Split by comma and extract URLs - handle wrapped content
            if content.startswith("FILE_URL:"):
                # Direct FILE_URL content
                parts = content.split(",")
            else:
                # Wrapped content - find all FILE_URL entries
                import re
                file_url_pattern = r'FILE_URL:[^\s,]+'
                matches = re.findall(file_url_pattern, content)
                parts = matches
            
            urls = []
            

            
            for i, part in enumerate(parts):
                part = part.strip()

                
                if part.startswith("FILE_URL:"):
                    url = part[9:]  # Remove "FILE_URL:" prefix

                    
                    if url and self._is_valid_url(url):
                        urls.append(url)

                    else:
                        logger.warning(f"Invalid URL found: {part} (extracted: {url[:50]}...)")
                else:
                    logger.warning(f"Part does not start with FILE_URL: {part}")
            
            logger.info(f"Extracted {len(urls)} file URLs from content")
            return urls
            
        except Exception as e:
            logger.error(f"Error extracting file URLs: {e}")
            return []
    
    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format and domain"""
        try:

            
            parsed = urlparse(url)

            
            # Only allow Coda-hosted files for security
            is_valid = (parsed.scheme in ['https'] and 
                       parsed.netloc in ['codahosted.io', 'coda.imgix.net'] and
                       len(url) > 10)
            

            if not is_valid:
                logger.warning(f"URL failed validation - scheme: {parsed.scheme}, netloc: {parsed.netloc}, length: {len(url)}")
            
            return is_valid
        except Exception as e:
            logger.error(f"URL validation error: {e}")
            return False
    
    def _get_mime_type(self, url: str, content_type: Optional[str] = None) -> str:
        """Determine MIME type from URL or content headers"""
        if content_type and content_type != 'application/octet-stream':
            return content_type
        
        # Extract file extension from URL
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        for ext, mime_type in self.supported_mime_types.items():
            if path.endswith(ext):
                return mime_type
        
        # Default to PDF if unknown
        return 'application/pdf'
    
    async def download_file(self, url: str) -> Tuple[bytes, str]:
        """Download a single file and return (data, mime_type)"""
        try:
            timeout = aiohttp.ClientTimeout(total=60)  # 60 second timeout
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(f"Downloading file from URL: {url[:50]}...")
                
                async with session.get(url) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}: Failed to download file")
                    
                    # Check content length
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > self.max_file_size:
                        raise Exception(f"File too large: {content_length} bytes > {self.max_file_size}")
                    
                    # Download file data
                    file_data = await response.read()
                    
                    # Verify size after download
                    if len(file_data) > self.max_file_size:
                        raise Exception(f"Downloaded file too large: {len(file_data)} bytes")
                    
                    # Determine MIME type
                    content_type = response.headers.get('Content-Type')
                    mime_type = self._get_mime_type(url, content_type)
                    
                    logger.info(f"Downloaded file: {len(file_data)} bytes, type: {mime_type}")
                    return file_data, mime_type
                    
        except asyncio.TimeoutError:
            logger.error(f"Timeout downloading file: {url}")
            raise Exception(f"File download timed out after 60 seconds")
        except Exception as e:
            logger.error(f"Error downloading file {url}: {e}")
            raise Exception(f"Failed to download file: {str(e)}")
    
    async def download_files(self, urls: List[str]) -> List[Dict[str, any]]:
        """Download multiple files concurrently"""
        if not urls:
            raise Exception("No file URLs provided")
        
        if len(urls) > 10:  # Reasonable limit
            raise Exception(f"Too many files: {len(urls)} (max 10)")
        
        try:
            logger.info(f"Starting download of {len(urls)} files")
            
            # Download all files concurrently
            download_tasks = [self.download_file(url) for url in urls]
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            
            files_data = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    raise Exception(f"File {i+1} download failed: {str(result)}")
                
                file_data, mime_type = result
                # Convert to base64 immediately for Claude API
                base64_data = base64.b64encode(file_data).decode('utf-8')
                
                files_data.append({
                    'data': file_data,
                    'base64_data': base64_data,  # Add base64 version for Claude API
                    'mime_type': mime_type,
                    'size': len(file_data),
                    'url': urls[i]
                })
            
            total_size = sum(f['size'] for f in files_data)
            logger.info(f"Successfully downloaded {len(files_data)} files, total size: {total_size} bytes")
            
            return files_data
            
        except Exception as e:
            logger.error(f"Error downloading files: {e}")
            raise
    
    def extract_text_content(self, file_data: bytes, mime_type: str, filename: str = "") -> str:
        """Extract text content from various file types"""
        try:
            logger.info(f"Extracting text content from {mime_type} file: {filename[:50]}...")
            
            if mime_type == 'text/plain':
                return file_data.decode('utf-8')
            
            elif mime_type == 'text/markdown':
                return file_data.decode('utf-8')
            
            elif mime_type == 'text/csv':
                return file_data.decode('utf-8')
            
            elif mime_type == 'text/vtt':
                return file_data.decode('utf-8')
            
            elif mime_type == 'application/json':
                return file_data.decode('utf-8')
                
            elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                try:
                    from docx import Document
                    doc = Document(BytesIO(file_data))
                    paragraphs = [paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip()]
                    return '\n\n'.join(paragraphs)
                except ImportError:
                    logger.error("python-docx not installed - cannot extract DOCX content")
                    raise Exception("DOCX processing requires python-docx library")
                except Exception as e:
                    logger.error(f"Error extracting DOCX content: {e}")
                    raise Exception(f"Failed to extract DOCX content: {str(e)}")
            
            else:
                logger.error(f"Unsupported file type for text extraction: {mime_type}")
                raise Exception(f"Text extraction not supported for {mime_type}. Supported types: PDF (as document block), plain text, markdown, CSV, VTT, JSON, DOCX")
                
        except UnicodeDecodeError as e:
            logger.error(f"Unicode decode error for {mime_type}: {e}")
            raise Exception(f"File encoding not supported - please ensure file is UTF-8 encoded")
        except Exception as e:
            logger.error(f"Text extraction failed for {mime_type}: {e}")
            raise Exception(f"Text extraction failed: {str(e)}")
