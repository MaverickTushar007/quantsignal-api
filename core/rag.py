"""
core/rag.py
Vector search over quant research papers stored in Supabase pgvector.
"""
from sentence_transformers import SentenceTransformer
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

_model = None
_client = None

def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def _get_client():
    global _client
    if _client is None:
        _client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )
    return _client

def search_research(query: str, top_k: int = 3) -> str:
    try:
        model = _get_model()
        client = _get_client()
        
        embedding = model.encode(query).tolist()
        
        result = client.rpc("match_research_chunks", {
            "query_embedding": embedding,
            "match_count": top_k
        }).execute()
        
        if not result.data:
            return ""
        
        chunks = [r["content"] for r in result.data]
        return "\n\n".join(chunks)
    
    except Exception as e:
        print(f"RAG lookup failed: {e}")
        return ""
