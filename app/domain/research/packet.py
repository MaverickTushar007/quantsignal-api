"""
domain/research/packet.py
The canonical data contract for all Perseus intelligence outputs.
Every API response, AI answer, and frontend card is built from a ResearchPacket.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class ConfidenceLevel(str, Enum):
    HIGH         = "high"
    MODERATE     = "moderate"
    LOW          = "low"
    INSUFFICIENT = "insufficient"


class PacketType(str, Enum):
    TICKER      = "ticker"
    EARNINGS    = "earnings"
    MACRO       = "macro"
    PORTFOLIO   = "portfolio"
    DOCUMENT    = "document"
    TRADE_SETUP = "trade_setup"


@dataclass
class EvidenceItem:
    source:    str            # "news" | "filing" | "price_action" | "model" | "macro"
    content:   str            # the actual evidence text
    timestamp: datetime
    weight:    float          # 0-1 how much this evidence matters
    url:       Optional[str] = None
    verified:  bool = False


@dataclass
class RiskFlag:
    category:             str   # "event_risk" | "regime_conflict" | "low_liquidity" | "earnings"
    severity:             str   # "high" | "medium" | "low"
    description:          str
    invalidation_trigger: Optional[str] = None


@dataclass
class ScenarioBranch:
    label:          str    # "bull_case" | "bear_case" | "base_case"
    probability:    float
    price_target:   Optional[float]
    narrative:      str
    key_assumption: str


@dataclass
class ResearchPacket:
    # Identity
    packet_id:         str
    packet_type:       PacketType
    symbol:            Optional[str]
    timestamp:         datetime
    freshness_seconds: int        # age of underlying data in seconds

    # Core answer
    summary:    str               # 2-3 sentence answer
    confidence: ConfidenceLevel

    # Evidence chain
    evidence: List[EvidenceItem] = field(default_factory=list)

    # Risk
    risk_flags: List[RiskFlag] = field(default_factory=list)

    # Scenarios
    scenarios: List[ScenarioBranch] = field(default_factory=list)

    # Market context
    regime:            Optional[str] = None
    regime_confidence: float = 0.0

    # Signal (if applicable)
    direction:      Optional[str]   = None
    probability:    Optional[float] = None
    expected_value: Optional[float] = None
    kelly_size:     Optional[float] = None
    take_profit:    Optional[float] = None
    stop_loss:      Optional[float] = None

    # Intellectual honesty
    open_questions:  List[str] = field(default_factory=list)
    contradictions:  List[str] = field(default_factory=list)

    # Audit trail
    model_used:          Optional[str] = None
    retrieval_sources:   List[str]     = field(default_factory=list)
    generation_latency_ms: int = 0

    # Verification
    claims_verified:    bool  = False
    numeric_checked:    bool  = False
    citation_coverage:  float = 0.0   # % of claims backed by evidence

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for API responses."""
        def _serialize(obj):
            if isinstance(obj, (ConfidenceLevel, PacketType)):
                return obj.value
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, list):
                return [_serialize(i) for i in obj]
            if hasattr(obj, '__dataclass_fields__'):
                return {k: _serialize(v) for k, v in obj.__dict__.items()}
            return obj

        return {k: _serialize(v) for k, v in self.__dict__.items()}


def _map_confidence(raw) -> ConfidenceLevel:
    """Map any confidence representation to ConfidenceLevel enum."""
    if raw is None:
        return ConfidenceLevel.INSUFFICIENT
    if isinstance(raw, ConfidenceLevel):
        return raw
    s = str(raw).lower()
    if s in ("high", "strong"):
        return ConfidenceLevel.HIGH
    if s in ("moderate", "medium"):
        return ConfidenceLevel.MODERATE
    if s in ("low", "weak"):
        return ConfidenceLevel.LOW
    # Numeric probability → confidence tier
    try:
        v = float(raw)
        if v >= 0.70:
            return ConfidenceLevel.HIGH
        if v >= 0.55:
            return ConfidenceLevel.MODERATE
        if v >= 0.45:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.INSUFFICIENT
    except (ValueError, TypeError):
        return ConfidenceLevel.INSUFFICIENT
