"""
Document embedder — Sprint 6 financial intelligence layer.
Uses fastembed (50MB) instead of sentence-transformers (2GB).
Embeds text chunks into 384-dim vectors for pgvector search.
"""
import os
import logging

log = logging.getLogger(__name__)
_model = None

def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        log.info("[embedder] fastembed model loaded")
    return _model

def embed_text(text: str) -> list[float]:
    model = get_model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist()

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks for better retrieval."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks
