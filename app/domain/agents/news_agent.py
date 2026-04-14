"""
agents/news_agent.py — live yfinance news + earnings flags for Perseus.
"""
import logging, os
from datetime import datetime, timezone
log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["NVDA","AAPL","TSLA","MSFT","AMZN","GOOGL","META","RELIANCE.NS","TCS.NS","BTC-USD","ETH-USD","SOL-USD","BNB-USD","GC=F","CL=F","EURUSD=X","USDINR=X"]

def run(symbols: list = None) -> dict:
    if symbols is None: symbols = DEFAULT_SYMBOLS
    findings = {"agent":"NewsAgent","run_at":datetime.now(timezone.utc).isoformat(),"catalysts":{},"headlines":{},"high_risk":[],"summary":""}
    for sym in symbols:
        catalyst, headlines = _get_catalyst_and_news(sym)
        if headlines: findings["headlines"][sym] = headlines
        if catalyst:
            findings["catalysts"][sym] = catalyst
            if catalyst.get("risk") == "high": findings["high_risk"].append(sym)
    n_news = sum(len(v) for v in findings["headlines"].values())
    findings["summary"] = (f"{n_news} live headlines across {len(findings['headlines'])} symbols. "
        f"{len(findings['catalysts'])} with catalysts. {len(findings['high_risk'])} HIGH risk: {', '.join(findings['high_risk']) or 'none'}.")
    _store(findings)
    return findings

def _get_catalyst_and_news(symbol: str) -> tuple:
    catalyst, headlines = {}, []
    try:
        import yfinance as yf
        for item in (yf.Ticker(symbol).news or [])[:3]:
            content = item.get("content", {})
            title = content.get("title") or item.get("title", "")
            if title: headlines.append(title[:150])
    except Exception: pass
    try:
        from app.domain.data.earnings import get_earnings_flag
        flag = get_earnings_flag(symbol)
        if flag:
            catalyst.update({"earnings_date":flag["date"],"days_to_earnings":flag["days_until"],"risk":"high" if flag["days_until"]<=7 else "medium","note":flag["warning"]})
    except Exception: pass
    try:
        import yfinance as yf
        beta = (yf.Ticker(symbol).info or {}).get("beta")
        if beta and beta > 1.8:
            catalyst["high_beta"] = round(beta, 2)
            catalyst["note"] = catalyst.get("note","") + f" High beta ({beta:.1f}) — amplified moves."
            catalyst.setdefault("risk","medium")
    except Exception: pass
    return (catalyst if catalyst else None), headlines

def _store(findings: dict):
    try:
        from supabase import create_client
        sb = create_client(os.environ.get("SUPABASE_URL",""), os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY",""))
        sb.table("agent_runs").upsert({"agent":"NewsAgent","run_at":findings["run_at"],"findings":findings}).execute()
        log.info(f"[NewsAgent] stored — {len(findings['headlines'])} symbols")
    except Exception as e:
        log.debug(f"[NewsAgent] store failed: {e}")
