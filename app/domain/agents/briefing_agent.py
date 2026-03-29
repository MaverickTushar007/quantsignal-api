"""
agents/briefing_agent.py
BriefingAgent — synthesizes all agent outputs into a morning briefing.
Called daily. Perseus narrates the briefing.
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run(user_id: str = "default") -> dict:
    """Generate morning briefing from all agent outputs."""
    from app.domain.agents.regime_agent import run as regime_run
    from app.domain.agents.risk_agent   import run as risk_run

    regime  = regime_run()
    risk    = risk_run()

    briefing = {
        "agent":      "BriefingAgent",
        "user_id":    user_id,
        "run_at":     datetime.now(timezone.utc).isoformat(),
        "regime":     regime,
        "risk":       risk,
        "alerts":     regime.get("alerts", []),
        "risk_level": risk.get("risk_level", "normal"),
        "commentary": "",
    }

    # Perseus narrates
    try:
        import groq
        key = os.environ.get("GROQ_API_KEY", "")
        if key:
            alerts    = briefing["alerts"]
            risk_lvl  = briefing["risk_level"]
            regime_map = regime.get("regime_map", {})
            n_bull    = sum(1 for v in regime_map.values() if v == "bull")
            n_bear    = sum(1 for v in regime_map.values() if v == "bear")
            n_ranging = sum(1 for v in regime_map.values() if v == "ranging")

            client = groq.Groq(api_key=key)
            resp   = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "system",
                    "content": "You are Perseus, QuantSignal's AI analyst. Write a concise morning briefing in 3 sentences."
                }, {
                    "role": "user",
                    "content": (
                        f"Market snapshot: {n_bull} bull, {n_bear} bear, {n_ranging} ranging symbols. "
                        f"Risk level: {risk_lvl}. "
                        f"High-conviction alerts: {len(alerts)}. "
                        f"Warnings: {risk.get('warnings', [])}. "
                        f"Write the morning briefing."
                    )
                }],
                max_tokens=150,
                temperature=0.3,
            )
            briefing["commentary"] = resp.choices[0].message.content.strip()
    except Exception as e:
        log.debug(f"[BriefingAgent] commentary failed: {e}")
        briefing["commentary"] = (
            f"Market risk level is {briefing['risk_level']}. "
            f"{len(briefing['alerts'])} high-conviction signals detected. "
            f"Monitor energy states and EV scores before entering positions."
        )

    _store(briefing)
    return briefing


def _store(briefing: dict):
    try:
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        sb.table("agent_runs").upsert({
            "agent":    "BriefingAgent",
            "run_at":   briefing["run_at"],
            "findings": briefing,
        }).execute()
    except Exception as e:
        log.debug(f"[BriefingAgent] store failed: {e}")
