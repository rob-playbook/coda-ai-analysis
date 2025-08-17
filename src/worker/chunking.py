# Updated chunking.py - Simplified for pre-built prompts
import tiktoken
from typing import List, Tuple
import re
import logging

logger = logging.getLogger(__name__)

class ContentChunker:
    def __init__(self, model_name: str = "claude-3-5-sonnet-20241022"):
        try:
            # Use GPT-4 tokenizer as approximation for Claude
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            # Fallback to basic tokenizer
            self.encoder = tiktoken.get_encoding("cl100k_base")
        
        self.max_tokens = 11000  # Conservative limit for Claude context
        self.overlap_tokens = 200  # Maintain context between chunks
        
    def chunk_content(self, content: str, user_prompt: str = "") -> List[str]:
        """
        Smart content chunking - only chunks the CONTENT, not the prompts
        Coda handles all prompt construction
        """
        try:
            # Estimate tokens for user prompt to reserve space
            prompt_tokens = len(self.encoder.encode(user_prompt)) if user_prompt else 1000
            
            # Adjust max tokens to account for prompt overhead
            available_tokens = self.max_tokens - prompt_tokens - 500  # Safety buffer
            
            if available_tokens <= 1000:
                logger.warning(f"Very little space left for content after prompt: {available_tokens} tokens")
                available_tokens = 1000  # Minimum content space
            
            return self._chunk_content_by_tokens(content, available_tokens)
            
        except Exception as e:
            logger.error(f"Content chunking failed: {e}")
            # Fallback to simple splitting
            return self._simple_fallback_chunking(content)
    
    def _chunk_content_by_tokens(self, content: str, max_content_tokens: int) -> List[str]:
        """
        Chunk content respecting token limits and semantic boundaries
        """
        # First, try to respect structured content boundaries
        blocks = self._extract_content_blocks(content)
        
        if not blocks:
            # No structured blocks found - use paragraph-based chunking
            return self._chunk_by_paragraphs(content, max_content_tokens)
        
        # Group blocks into chunks under token limit
        chunks = []
        current_chunk = ""
        current_tokens = 0
        
        for block in blocks:
            block_tokens = len(self.encoder.encode(block))
            
            # Check if single block exceeds limit
            if block_tokens > max_content_tokens:
                logger.warning(f"Single block exceeds token limit: {block_tokens} tokens")
                # Add current chunk if not empty
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_tokens = 0
                # Split large block by paragraphs
                sub_chunks = self._chunk_by_paragraphs(block, max_content_tokens)
                chunks.extend(sub_chunks)
                continue
            
            if current_tokens + block_tokens > max_content_tokens and current_chunk:
                # Current chunk would exceed limit - save it and start new one
                chunks.append(current_chunk.strip())
                current_chunk = block
                current_tokens = block_tokens
            else:
                # Add block to current chunk
                current_chunk += "\n\n" + block if current_chunk else block
                current_tokens += block_tokens
        
        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks if chunks else [content]
    
    def _extract_content_blocks(self, content: str) -> List[str]:
        """
        Extract content blocks respecting <block> boundaries
        """
        # Pattern to match content within < > brackets
        pattern = r'<([^<>]+?)>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if matches:
            return [f"<{match.strip()}>" for match in matches if match.strip()]
        
        # No bracketed content found - split by double newlines
        blocks = [block.strip() for block in content.split('\n\n') if block.strip()]
        return blocks if blocks else [content]
    
    def _chunk_by_paragraphs(self, content: str, max_tokens: int) -> List[str]:
        """
        Fallback chunking by paragraphs when no structure detected
        """
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        chunks = []
        current_chunk = ""
        current_tokens = 0
        
        for paragraph in paragraphs:
            para_tokens = len(self.encoder.encode(paragraph))
            
            # Check if single paragraph exceeds limit
            if para_tokens > max_tokens:
                logger.warning(f"Single paragraph exceeds token limit: {para_tokens} tokens")
                # Add current chunk if not empty
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_tokens = 0
                # Split large paragraph by sentences
                chunks.extend(self._chunk_by_sentences(paragraph, max_tokens))
                continue
            
            if current_tokens + para_tokens > max_tokens and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = paragraph
                current_tokens = para_tokens
            else:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph
                current_tokens += para_tokens
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks if chunks else [content]
    
    def _chunk_by_sentences(self, paragraph: str, max_tokens: int) -> List[str]:
        """
        Split large paragraph by sentences
        """
        sentences = [s.strip() + '.' for s in paragraph.split('.') if s.strip()]
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            sentence_tokens = len(self.encoder.encode(sentence))
            
            if len(self.encoder.encode(current_chunk + " " + sentence)) <= max_tokens:
                current_chunk += " " + sentence if current_chunk else sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def _simple_fallback_chunking(self, content: str) -> List[str]:
        """
        Simple character-based chunking as last resort
        """
        chunk_size = self.max_tokens * 4  # Rough character estimate
        chunks = []
        
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            chunks.append(chunk)
        
        return chunks
