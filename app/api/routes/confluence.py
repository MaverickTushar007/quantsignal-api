import logging, os
from fastapi import APIRouter, HTTPException
router = APIRouter()
log = logging.getLogger(__name__)

def _sb():
    from supabase import create_client
    return create_client(os.environ.get("SUPABASE_URL",""), os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY",""))

@router.get("/admin/confluence/latest")
def confluence_latest():
    try:
        res = _sb().table("agent_runs").select("findings,run_at").eq("agent","ConfluenceVerifier").order("run_at",desc=True).limit(1).execute()
        if not res.data:
            return {"note":"No ConfluenceVerifier runs found yet."}
        f = res.data[0].get("findings") or {}
        f.setdefault("run_at", res.data[0].get("run_at"))
        return f
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/admin/confluence/history")
def confluence_history(limit: int = 10):
    try:
        res = _sb().table("agent_runs").select("findings,run_at").eq("agent","ConfluenceVerifier").order("run_at",desc=True).limit(limit).execute()
        runs = [{"run_at":r.get("run_at"),"total_signals":(r.get("findings") or {}).get("total_signals",0),"conflict_pct":((r.get("findings") or {}).get("direction_conflicts") or {}).get("pct",0),"coverage":{s:v.get("pct",0) for s,v in ((r.get("findings") or {}).get("coverage") or {}).items()}} for r in res.data or []]
        return {"agent":"ConfluenceVerifier","count":len(runs),"runs":runs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/admin/confluence/examples")
def confluence_examples(limit: int = 20):
    try:
        res = _sb().table("agent_runs").select("findings,run_at").eq("agent","ConfluenceVerifier").order("run_at",desc=True).limit(1).execute()
        if not res.data:
            return {"examples":[]}
        f  = res.data[0].get("findings") or {}
        dc = f.get("direction_conflicts") or {}
        return {"run_at":res.data[0].get("run_at"),"examples":(dc.get("examples") or [])[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
