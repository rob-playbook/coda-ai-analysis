# src/worker/chunking.py
import tiktoken
from typing import List
import re
import logging

logger = logging.getLogger(__name__)

class ContentChunker:
    def __init__(self):
        try:
            # Use GPT-4 tokenizer as approximation for Claude
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            # Fallback to basic tokenizer
            self.encoder = tiktoken.get_encoding("cl100k_base")
        
        self.max_tokens = 11000  # Conservative limit for Claude
        
    def chunk_content(self, content: str, is_iteration: bool = False, 
                     iteration_content: str = None) -> List[str]:
        """Smart content chunking with semantic boundary respect"""
        try:
            if is_iteration:
                return self._chunk_iteration_content(content, iteration_content)
            else:
                return self._chunk_regular_content(content)
        except Exception as e:
            logger.error(f"Content chunking failed: {e}")
            return self._simple_fallback_chunking(content)
    
    def _chunk_regular_content(self, content: str) -> List[str]:
        """Regular mode: Split analysis context content intelligently"""
        # Extract content blocks (respecting <block> boundaries)
        blocks = self._extract_content_blocks(content)
        
        if not blocks:
            return self._chunk_by_paragraphs(content)
        
        # Group blocks into chunks under token limit
        chunks = []
        current_chunk = ""
        current_tokens = 0
        
        for block in blocks:
            block_tokens = len(self.encoder.encode(block))
            
            if block_tokens > self.max_tokens:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_tokens = 0
                sub_chunks = self._chunk_by_paragraphs(block)
                chunks.extend(sub_chunks)
                continue
            
            if current_tokens + block_tokens > self.max_tokens and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = block
                current_tokens = block_tokens
            else:
                current_chunk += "\n\n" + block if current_chunk else block
                current_tokens += block_tokens
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks if chunks else [content]
    
    def _chunk_iteration_content(self, analysis_content: str, iteration_content: str) -> List[str]:
        """Iteration mode: Validation content + analysis content structure"""
        validation_section = f"**CONTENT TO VALIDATE:**\n{iteration_content}"
        analysis_section = f"**ITEMS FOR ANALYSIS:**\n{analysis_content}"
        
        validation_tokens = len(self.encoder.encode(validation_section))
        analysis_tokens = len(self.encoder.encode(analysis_section))
        
        # Check if both sections fit in single chunk
        if validation_tokens + analysis_tokens <= self.max_tokens:
            return [f"{validation_section}\n\n{analysis_section}"]
        
        # Need to split - preserve validation section integrity
        chunks = []
        
        # First chunk: validation + as much analysis as possible
        analysis_blocks = self._extract_content_blocks(analysis_content)
        first_chunk = validation_section + "\n\n**ITEMS FOR ANALYSIS:**\n"
        current_tokens = len(self.encoder.encode(first_chunk))
        
        used_blocks = 0
        for i, block in enumerate(analysis_blocks):
            block_tokens = len(self.encoder.encode(block))
            if current_tokens + block_tokens <= self.max_tokens:
                first_chunk += block + "\n\n"
                current_tokens += block_tokens
                used_blocks = i + 1
            else:
                break
        
        chunks.append(first_chunk.strip())
        
        # Remaining chunks: analysis content only
        remaining_blocks = analysis_blocks[used_blocks:]
        if remaining_blocks:
            remaining_content = "\n\n".join(remaining_blocks)
            additional_chunks = self._chunk_regular_content(remaining_content)
            chunks.extend(additional_chunks)
        
        return chunks
    
    def _extract_content_blocks(self, content: str) -> List[str]:
        """Extract content blocks respecting <block> boundaries"""
        pattern = r'<([^<>]+?)>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if matches:
            return [f"<{match.strip()}>" for match in matches if match.strip()]
        
        # No bracketed content - split by double newlines
        blocks = [block.strip() for block in content.split('\n\n') if block.strip()]
        return blocks if blocks else [content]
    
    def _chunk_by_paragraphs(self, content: str) -> List[str]:
        """Fallback chunking by paragraphs"""
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        chunks = []
        current_chunk = ""
        current_tokens = 0
        
        for paragraph in paragraphs:
            para_tokens = len(self.encoder.encode(paragraph))
            
            if para_tokens > self.max_tokens:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_tokens = 0
                # Split large paragraph by sentences
                sentences = paragraph.split('. ')
                temp_chunk = ""
                for sentence in sentences:
                    if len(self.encoder.encode(temp_chunk + sentence)) <= self.max_tokens:
                        temp_chunk += sentence + ". "
                    else:
                        if temp_chunk:
                            chunks.append(temp_chunk.strip())
                        temp_chunk = sentence + ". "
                if temp_chunk:
                    chunks.append(temp_chunk.strip())
                continue
            
            if current_tokens + para_tokens > self.max_tokens and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = paragraph
                current_tokens = para_tokens
            else:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph
                current_tokens += para_tokens
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks if chunks else [content]
    
    def _simple_fallback_chunking(self, content: str) -> List[str]:
        """Simple character-based chunking as last resort"""
        chunk_size = self.max_tokens * 4  # Rough character estimate
        chunks = []
        
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            chunks.append(chunk)
        
        return chunks