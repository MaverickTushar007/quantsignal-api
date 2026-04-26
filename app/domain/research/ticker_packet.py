"""
domain/research/ticker_packet.py
Builds a ResearchPacket for a single ticker by wiring together
all existing domain services: signal, regime, news, earnings, conflicts.
This is the entry point for the Perseus intelligence layer.
"""
import uuid
import logging
from datetime import datetime, timezone

from app.domain.research.packet import (
    ResearchPacket, EvidenceItem, RiskFlag,
    PacketType, _map_confidence,
)

log = logging.getLogger(__name__)


async def build_ticker_packet(symbol: str) -> ResearchPacket:
    """
    Assembles a full ResearchPacket for a symbol.
    Never raises — degrades gracefully if any domain call fails.
    """
    packet_id  = str(uuid.uuid4())
    evidence:   list = []
    risk_flags: list = []
    contradictions: list = []
    open_questions: list = []
    now = datetime.now(timezone.utc)

    # ── 1. Signal pipeline ────────────────────────────────────────────────
    signal = {}
    try:
        from app.domain.signal.service import generate_signal
        signal = generate_signal(symbol) or {}
    except Exception as e:
        log.warning(f"[ticker_packet] signal failed for {symbol}: {e}")
        open_questions.append("Signal pipeline unavailable — check data feed.")

    direction      = signal.get("direction")
    probability    = signal.get("probability")
    expected_value = signal.get("ev_score") or signal.get("expected_value")
    kelly_size     = signal.get("kelly_size")
    take_profit    = signal.get("take_profit")
    stop_loss      = signal.get("stop_loss")
    confidence_raw = signal.get("confidence", probability)

    if signal:
        evidence.append(EvidenceItem(
            source="model",
            content=signal.get("one_liner") or f"{direction} signal, prob={probability:.2f}" if probability else f"{direction} signal",
            timestamp=now,
            weight=0.9,
            verified=True,
        ))

    # ── 2. Regime ─────────────────────────────────────────────────────────
    regime_label = None
    regime_conf  = 0.0
    try:
        from app.domain.regime.detector import detect_regime
        regime_data   = detect_regime(symbol) or {}
        regime_label  = regime_data.get("regime")
        regime_conf   = float(regime_data.get("confidence", 0))
        if regime_label:
            evidence.append(EvidenceItem(
                source="model",
                content=f"Current regime: {regime_label} (confidence {regime_conf:.0%})",
                timestamp=now,
                weight=0.7,
                verified=True,
            ))
            # Flag regime conflict
            if direction == "BUY" and regime_label in ("BEAR", "HIGH_VOL"):
                risk_flags.append(RiskFlag(
                    category="regime_conflict",
                    severity="high",
                    description=f"BUY signal in {regime_label} regime — reduced edge",
                    invalidation_trigger="Regime shift to BULL or LOW_VOL",
                ))
            elif direction == "SELL" and regime_label in ("BULL", "LOW_VOL"):
                risk_flags.append(RiskFlag(
                    category="regime_conflict",
                    severity="medium",
                    description=f"SELL signal in {regime_label} regime — reduced edge",
                ))
    except Exception as e:
        log.warning(f"[ticker_packet] regime failed for {symbol}: {e}")
        open_questions.append("Regime detection unavailable.")

    # ── 3. News ───────────────────────────────────────────────────────────
    try:
        from app.domain.data.news import get_news
        news_items = get_news(symbol, limit=5) or []
        for item in news_items:
            evidence.append(EvidenceItem(
                source="news",
                content=getattr(item, "title", "") or "",
                timestamp=now,
                weight=0.6,
                url=getattr(item, "url", None),
                verified=True,
            ))
    except Exception as e:
        log.warning(f"[ticker_packet] news failed for {symbol}: {e}")

    # ── 4. Earnings risk ──────────────────────────────────────────────────
    try:
        from app.domain.data.earnings import get_earnings_flag
        earnings = get_earnings_flag(symbol) or {}
        days_until = earnings.get("days_until_earnings", 999)
        if days_until < 14:
            severity = "high" if days_until < 5 else "medium"
            risk_flags.append(RiskFlag(
                category="event_risk",
                severity=severity,
                description=f"Earnings in {days_until} days — binary event risk",
                invalidation_trigger="Size small or wait for post-earnings confirmation",
            ))
    except Exception as e:
        log.warning(f"[ticker_packet] earnings failed for {symbol}: {e}")

    # ── 5. Conflict detection ─────────────────────────────────────────────
    try:
        from app.domain.agents.conflict_agent import get_conflict_map
        conflict_map = get_conflict_map() or {}
        sym_conflicts = conflict_map.get(symbol, {})
        if sym_conflicts.get("conflicts"):
            for c in sym_conflicts["conflicts"]:
                desc = str(c.get("reason", c))
                contradictions.append(desc)
                risk_flags.append(RiskFlag(
                    category="signal_conflict",
                    severity=c.get("severity", "medium") if isinstance(c, dict) else "medium",
                    description=desc,
                ))
    except Exception as e:
        log.warning(f"[ticker_packet] conflict check failed for {symbol}: {e}")

    # ── 6. Circuit breaker ────────────────────────────────────────────────
    try:
        from app.domain.core.circuit_breaker_v2 import check_daily_loss_limit
        tripped, loss_pct = check_daily_loss_limit()
        if tripped:
            risk_flags.append(RiskFlag(
                category="circuit_breaker",
                severity="high",
                description=f"Daily loss limit hit ({loss_pct:.1%}) — circuit breaker active",
                invalidation_trigger="Reset after win streak or manual override",
            ))
    except Exception as e:
        log.warning(f"[ticker_packet] circuit breaker check failed: {e}")

    # ── 7. Summary ────────────────────────────────────────────────────────
    # ── W2.2 Freshness confidence downgrade ─────────────────────────────
    try:
        from app.domain.data.freshness import confidence_after_staleness, staleness_label, is_stale
        age_s = int(signal.get("data_age_seconds", 60)) if signal else 3600
        raw_conf = _map_confidence(confidence_raw)
        downgraded_conf = confidence_after_staleness(raw_conf.value, age_s, "signal")
        if downgraded_conf != raw_conf.value:
            from app.domain.research.packet import ConfidenceLevel
            confidence_raw = downgraded_conf
            risk_flags.append(RiskFlag(
                category="data_freshness",
                severity="medium",
                description=f"Signal data is {staleness_label(age_s)} — confidence downgraded",
                invalidation_trigger="Refresh signal or wait for next data cycle",
            ))
    except Exception as _fe:
        log.warning(f"[ticker_packet] freshness downgrade failed: {_fe}")

    # ── W2.2 Freshness confidence downgrade ─────────────────────────────
    try:
        from app.domain.data.freshness import confidence_after_staleness, staleness_label, is_stale
        age_s = int(signal.get("data_age_seconds", 60)) if signal else 3600
        raw_conf = _map_confidence(confidence_raw)
        downgraded_conf = confidence_after_staleness(raw_conf.value, age_s, "signal")
        if downgraded_conf != raw_conf.value:
            from app.domain.research.packet import ConfidenceLevel
            confidence_raw = downgraded_conf
            risk_flags.append(RiskFlag(
                category="data_freshness",
                severity="medium",
                description=f"Signal data is {staleness_label(age_s)} — confidence downgraded",
                invalidation_trigger="Refresh signal or wait for next data cycle",
            ))
    except Exception as _fe:
        log.warning(f"[ticker_packet] freshness downgrade failed: {_fe}")

    summary = _build_summary(symbol, direction, probability, regime_label,
                              risk_flags, contradictions)

    return ResearchPacket(
        packet_id=packet_id,
        packet_type=PacketType.TICKER,
        symbol=symbol,
        timestamp=now,
        freshness_seconds=int(signal.get('data_age_seconds', 60)) if signal else 3600,
        summary=summary,
        confidence=_map_confidence(confidence_raw),
        evidence=evidence,
        risk_flags=risk_flags,
        regime=regime_label,
        regime_confidence=regime_conf,
        direction=direction,
        probability=probability,
        expected_value=expected_value,
        kelly_size=kelly_size,
        take_profit=take_profit,
        stop_loss=stop_loss,
        open_questions=open_questions,
        contradictions=contradictions,
        model_used="ensemble_v2+confluence_v2",
        claims_verified=False,
        numeric_checked=False,
        citation_coverage=len(evidence) / max(len(evidence) + 1, 1),
    )


def _build_summary(symbol, direction, probability, regime, risk_flags, contradictions) -> str:
    parts = []
    if direction and probability:
        pct = f"{probability:.0%}"
        parts.append(f"{symbol}: {direction} signal with {pct} probability.")
    elif direction:
        parts.append(f"{symbol}: {direction} signal.")
    else:
        parts.append(f"{symbol}: No clear signal.")

    if regime:
        parts.append(f"Current regime is {regime}.")

    high_risks = [r for r in risk_flags if r.severity == "high"]
    if high_risks:
        parts.append(f"⚠ {high_risks[0].description}.")

    if contradictions:
        parts.append(f"Conflict detected: {contradictions[0]}.")

    return " ".join(parts)
