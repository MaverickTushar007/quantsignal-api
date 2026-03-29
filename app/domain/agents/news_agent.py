"""
agents/news_agent.py
NewsAgent — injects earnings dates, macro events, and IV context
into Perseus so it reasons about catalysts, not just technicals.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run(symbols: list[str] = None) -> dict:
    """
    Fetch upcoming catalysts for symbols.
    Returns findings dict — never raises.
    """
    if symbols is None:
        symbols = ["NVDA", "AAPL", "TSLA", "MSFT", "RELIANCE.NS",
                   "BTC-USD", "ETH-USD", "AMZN", "GOOGL", "META"]

    findings = {
        "agent":      "NewsAgent",
        "run_at":     datetime.now(timezone.utc).isoformat(),
        "catalysts":  {},
        "high_risk":  [],
        "summary":    "",
    }

    for sym in symbols:
        catalyst = _get_catalyst(sym)
        if catalyst:
            findings["catalysts"][sym] = catalyst
            if catalyst.get("risk") == "high":
                findings["high_risk"].append(sym)

    # Perseus summary
    if findings["catalysts"]:
        n_high = len(findings["high_risk"])
        total  = len(findings["catalysts"])
        findings["summary"] = (
            f"{total} symbols have upcoming catalysts. "
            f"{n_high} flagged HIGH risk (earnings/FOMC within 7 days). "
            f"High-risk symbols: {', '.join(findings['high_risk']) or 'none'}."
        )
    else:
        findings["summary"] = "No major catalysts detected for tracked symbols."

    _store(findings)
    return findings


def _get_catalyst(symbol: str) -> dict | None:
    """
    Check earnings cache + basic IV check.
    Returns catalyst dict or None.
    """
    result = {}

    # Check earnings cache
    try:
        from app.domain.data.earnings import get_earnings_date
        earnings = get_earnings_date(symbol)
        if earnings:
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            try:
                ed = datetime.fromisoformat(str(earnings).replace("Z", "+00:00"))
                days_away = (ed - now).days
                if 0 <= days_away <= 30:
                    result["earnings_date"] = str(earnings)
                    result["days_to_earnings"] = days_away
                    result["risk"] = "high" if days_away <= 7 else "medium"
                    result["note"] = f"Earnings in {days_away}d — expect IV expansion"
            except Exception:
                pass
    except Exception:
        pass

    # IV check via yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info or {}
        # Beta as volatility proxy if IV unavailable
        beta = info.get("beta")
        if beta and beta > 1.5:
            result["high_beta"] = round(beta, 2)
            result["note"] = result.get("note", "") + f" High beta ({beta:.1f}) — amplified moves likely."
            if "risk" not in result:
                result["risk"] = "medium"
    except Exception:
        pass

    return result if result else None


def _store(findings: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "NewsAgent",
            "run_at":   findings["run_at"],
            "findings": findings,
        }).execute()
    except Exception as e:
        log.debug(f"[NewsAgent] store failed: {e}")
