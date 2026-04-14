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
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            _model = None
    return _model

def _get_client():
    global _client
    if _client is None:
        _client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )
    return _client

GS_PAPERS = {
    "derman_1999_vol_regimes", "derman_1994_local_vol", "goldman_var_framework",
    "goldman_stat_arb", "goldman_momentum_factors", "goldman_regime_detection"
}

def search_research(query: str, top_k: int = 3, mode: str = "auto") -> str:
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
        
        if mode == "quant":
            # Prioritise GS papers, truncate to 300 chars each
            gs_chunks = [r["content"][:300] for r in result.data if r.get("paper") in GS_PAPERS]
            other_chunks = [r["content"][:200] for r in result.data if r.get("paper") not in GS_PAPERS]
            chunks = (gs_chunks + other_chunks)[:2]
        else:
            chunks = [r["content"][:250] for r in result.data][:2]
        
        return "\n\n".join(chunks)
    
    except Exception as e:
        print(f"RAG lookup failed: {e}")
        return ""
