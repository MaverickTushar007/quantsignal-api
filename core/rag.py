"""
core/rag.py
Loads the pre-built FAISS index and provides a semantic search interface.
"""
import faiss
import json
import numpy as np
from sentence_transformers import SentenceTransformer
from pathlib import Path

# Paths
INDEX_FILE = Path("ml/rag_index.faiss")
CHUNKS_FILE = Path("ml/rag_chunks.json")

# Global state to prevent reloading on each request
_model = None
_index = None
_chunks = None

def _initialize():
    global _model, _index, _chunks
    if _model is not None:
        return

    print("Initializing Quant RAG...")
    try:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        _index = faiss.read_index(str(INDEX_FILE))
        with open(CHUNKS_FILE, 'r') as f:
            _chunks = json.load(f)
    except Exception as e:
        print(f"RAG Initialization failed: {e}")
        _model, _index, _chunks = None, None, None

def search_research(query: str, top_k: int = 2) -> list[str]:
    """
    Search the quant corpus for the most relevant context chunks.
    Returns a list of raw text contexts.
    """
    _initialize()
    if not _model or not _index or not _chunks:
        return []

    # Embed the search query
    query_vector = _model.encode([query])
    query_vector = np.array(query_vector).astype("float32")

    # Search FAISS
    distances, indices = _index.search(query_vector, top_k)

    results = []
    for idx in indices[0]:
        if idx != -1 and idx < len(_chunks):
            results.append(_chunks[idx]["full_context"])
            
    return results
