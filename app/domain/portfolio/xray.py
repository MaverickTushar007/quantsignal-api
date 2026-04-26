"""
domain/portfolio/xray.py
Phase 5 — Portfolio X-Ray Engine.
Analyzes a user's holdings for: concentration risk, sector crowding,
regime misalignment, estimated volatility, and suggested actions.
Wraps the existing allocator — does not replace it.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

log = logging.getLogger(__name__)

# Sector concentration thresholds
SECTOR_WARN_THRESHOLD  = 0.35   # warn if single sector > 35%
SECTOR_HIGH_THRESHOLD  = 0.50   # high risk if > 50%

# Position concentration thresholds
TOP5_WARN_THRESHOLD    = 0.60
TOP5_HIGH_THRESHOLD    = 0.75

# Regime → unfavorable signal direction
REGIME_CONFLICTS = {
    "BEAR":     "BUY",
    "HIGH_VOL": "BUY",
    "BULL":     "SELL",
}

# Rough annualized vol estimates by asset class (for portfolio vol estimate)
ASSET_VOL_MAP = {
    "crypto": 0.80,
    "us_tech": 0.35,
    "india_large": 0.25,
    "india_mid":   0.35,
    "gold":        0.15,
    "default":     0.30,
}


@dataclass
class PositionAlert:
    symbol:   str
    severity: str   # "high" | "medium" | "low"
    category: str   # "concentration" | "regime_conflict" | "sector" | "earnings"
    message:  str
    action:   str


@dataclass
class PortfolioXRay:
    # Exposure
    total_value:       float
    long_value:        float
    short_value:       float
    net_exposure_pct:  float   # net / total

    # Concentration
    top5_concentration:    float
    largest_position_pct:  float
    largest_position:      str

    # Sector
    sector_breakdown:      Dict[str, float]   # sector → % of portfolio
    most_crowded_sector:   str
    most_crowded_pct:      float

    # Risk
    estimated_annual_vol:  float
    estimated_10pct_loss:  float   # loss in base currency in 10% drawdown
    regime_fit_score:      float   # 0-1 (1 = fully aligned with regime)

    # Misalignment
    misaligned_positions:  List[str]
    misaligned_count:      int

    # Alerts and actions
    alerts:            List[PositionAlert] = field(default_factory=list)
    suggested_actions: List[str]           = field(default_factory=list)

    # Meta
    current_regime:    Optional[str] = None
    holdings_count:    int = 0

    def to_dict(self) -> dict:
        return {
            "total_value":          round(self.total_value, 2),
            "long_value":           round(self.long_value, 2),
            "short_value":          round(self.short_value, 2),
            "net_exposure_pct":     round(self.net_exposure_pct, 3),
            "top5_concentration":   round(self.top5_concentration, 3),
            "largest_position_pct": round(self.largest_position_pct, 3),
            "largest_position":     self.largest_position,
            "sector_breakdown":     {k: round(v, 3) for k, v in self.sector_breakdown.items()},
            "most_crowded_sector":  self.most_crowded_sector,
            "most_crowded_pct":     round(self.most_crowded_pct, 3),
            "estimated_annual_vol": round(self.estimated_annual_vol, 3),
            "correlation_pairs": self.correlation_pairs,
            "estimated_10pct_loss": round(self.estimated_10pct_loss, 2),
            "regime_fit_score":     round(self.regime_fit_score, 3),
            "misaligned_positions": self.misaligned_positions,
            "misaligned_count":     self.misaligned_count,
            "current_regime":       self.current_regime,
            "holdings_count":       self.holdings_count,
            "alerts": [
                {
                    "symbol":   a.symbol,
                    "severity": a.severity,
                    "category": a.category,
                    "message":  a.message,
                    "action":   a.action,
                }
                for a in self.alerts
            ],
            "suggested_actions": self.suggested_actions,
        }


class PortfolioXRayEngine:

    def analyze(
        self,
        holdings: List[Dict],
        current_signals: Dict = None,
        current_regime: str = None,
    ) -> PortfolioXRay:
        """
        holdings: list of dicts with keys:
            symbol, value (in base currency), side ("LONG"/"SHORT"),
            sector (optional), asset_class (optional)
        current_signals: dict of symbol → signal dict (from generate_signal)
        current_regime:  string like "BULL", "BEAR", "HIGH_VOL"
        """
        if not holdings:
            return self._empty_xray(current_regime)

        current_signals = current_signals or {}
        alerts: List[PositionAlert] = []
        suggested_actions: List[str] = []

        total_value = sum(h.get("value", 0) for h in holdings)
        if total_value <= 0:
            return self._empty_xray(current_regime)

        long_value  = sum(h["value"] for h in holdings if h.get("side", "LONG") == "LONG")
        short_value = sum(h["value"] for h in holdings if h.get("side") == "SHORT")

        # ── Concentration ──────────────────────────────────────────────
        sorted_holdings = sorted(holdings, key=lambda x: x.get("value", 0), reverse=True)
        top5_value      = sum(h["value"] for h in sorted_holdings[:5])
        top5_conc       = top5_value / total_value

        largest         = sorted_holdings[0]
        largest_pct     = largest["value"] / total_value
        largest_symbol  = largest.get("symbol", "?")

        if top5_conc > TOP5_HIGH_THRESHOLD:
            alerts.append(PositionAlert(
                symbol="PORTFOLIO", severity="high", category="concentration",
                message=f"Top 5 positions = {top5_conc:.0%} of portfolio — extreme concentration",
                action="Reduce largest 2 positions by 20% each",
            ))
        elif top5_conc > TOP5_WARN_THRESHOLD:
            alerts.append(PositionAlert(
                symbol="PORTFOLIO", severity="medium", category="concentration",
                message=f"Top 5 positions = {top5_conc:.0%} of portfolio — high concentration",
                action="Consider trimming top 2 positions",
            ))

        if largest_pct > 0.25:
            alerts.append(PositionAlert(
                symbol=largest_symbol, severity="high", category="concentration",
                message=f"{largest_symbol} = {largest_pct:.0%} of portfolio — exceeds 25% limit",
                action=f"Trim {largest_symbol} to under 25%",
            ))

        # ── Sector breakdown ───────────────────────────────────────────
        sector_map: Dict[str, float] = {}
        for h in holdings:
            sector = h.get("sector") or self._infer_sector(h.get("symbol", ""))
            sector_map[sector] = sector_map.get(sector, 0) + h["value"] / total_value

        most_crowded_sector = max(sector_map, key=sector_map.get) if sector_map else "Unknown"
        most_crowded_pct    = sector_map.get(most_crowded_sector, 0)

        if most_crowded_pct > SECTOR_HIGH_THRESHOLD:
            alerts.append(PositionAlert(
                symbol="PORTFOLIO", severity="high", category="sector",
                message=f"{most_crowded_sector} sector = {most_crowded_pct:.0%} — dangerous concentration",
                action=f"Add defensive exposure (FMCG/Pharma/Gold) to offset {most_crowded_sector} risk",
            ))
        elif most_crowded_pct > SECTOR_WARN_THRESHOLD:
            alerts.append(PositionAlert(
                symbol="PORTFOLIO", severity="medium", category="sector",
                message=f"{most_crowded_sector} sector = {most_crowded_pct:.0%} — elevated sector concentration",
                action=f"Consider diversifying outside {most_crowded_sector}",
            ))

        # ── Regime misalignment ────────────────────────────────────────
        misaligned: List[str] = []
        conflict_direction = REGIME_CONFLICTS.get(current_regime or "", None)

        for h in holdings:
            sym  = h.get("symbol", "")
            side = h.get("side", "LONG")
            sig  = current_signals.get(sym, {})
            sig_dir = sig.get("direction", "")

            if conflict_direction and sig_dir == conflict_direction and side == "LONG":
                misaligned.append(sym)
                alerts.append(PositionAlert(
                    symbol=sym, severity="medium", category="regime_conflict",
                    message=f"{sym}: {sig_dir} signal conflicts with {current_regime} regime",
                    action=f"Reduce {sym} or add hedge — signal contradicts current regime",
                ))

        regime_fit = 1.0 - (len(misaligned) / len(holdings)) if holdings else 1.0

        # ── Estimated portfolio volatility ─────────────────────────────
        weighted_vol = 0.0
        for h in holdings:
            asset_class = h.get("asset_class", "default")
            vol = ASSET_VOL_MAP.get(asset_class, ASSET_VOL_MAP["default"])
            weighted_vol += vol * (h["value"] / total_value)

        # W3.1 — Real pairwise correlation over 60-day window
        correlation_pairs = []
        try:
            import yfinance as yf
            syms = [h["symbol"] for h in holdings if h.get("symbol")]
            if len(syms) >= 2:
                raw = yf.download(syms, period="60d", progress=False, auto_adjust=True)
                closes = raw["Close"] if "Close" in raw.columns else raw
                if hasattr(closes, "columns") and len(closes.columns) >= 2:
                    corr_matrix = closes.pct_change().dropna().corr()
                    for i, s1 in enumerate(corr_matrix.columns):
                        for j, s2 in enumerate(corr_matrix.columns):
                            if j <= i:
                                continue
                            c = round(float(corr_matrix.loc[s1, s2]), 3)
                            correlation_pairs.append({"s1": s1, "s2": s2, "correlation": c})
                            if abs(c) > 0.75:
                                alerts.append(PositionAlert(
                                    symbol=f"{s1}/{s2}",
                                    severity="high" if abs(c) > 0.88 else "medium",
                                    category="correlation_risk",
                                    message=f"{s1} and {s2} are {c:.0%} correlated (60d) — hidden concentration",
                                    action="Consider replacing one with a lower-correlation alternative",
                                ))
        except Exception as _ce:
            log.warning(f"[xray] correlation calc failed: {_ce}")

        # Simple vol estimate — conservative overestimate
        estimated_loss_10pct = total_value * 0.10

        # ── Suggested actions ──────────────────────────────────────────
        if misaligned:
            suggested_actions.append(
                f"Reduce or hedge {len(misaligned)} position(s) conflicting with {current_regime} regime: "
                f"{', '.join(misaligned[:3])}"
            )
        if top5_conc > TOP5_WARN_THRESHOLD:
            suggested_actions.append(
                f"Reduce top-5 concentration from {top5_conc:.0%} toward 50% — trim {sorted_holdings[0].get('symbol','?')} first"
            )
        if most_crowded_pct > SECTOR_WARN_THRESHOLD:
            suggested_actions.append(
                f"Add non-{most_crowded_sector} exposure — consider FMCG, Pharma, or Gold as regime hedge"
            )
        if not suggested_actions:
            suggested_actions.append("Portfolio looks well-diversified — maintain current allocation")

        return PortfolioXRay(
            total_value=total_value,
            long_value=long_value,
            short_value=short_value,
            net_exposure_pct=(long_value - short_value) / total_value,
            top5_concentration=top5_conc,
            largest_position_pct=largest_pct,
            largest_position=largest_symbol,
            sector_breakdown=sector_map,
            most_crowded_sector=most_crowded_sector,
            most_crowded_pct=most_crowded_pct,
            estimated_annual_vol=weighted_vol,
            correlation_pairs=correlation_pairs,
            estimated_10pct_loss=estimated_loss_10pct,
            regime_fit_score=regime_fit,
            misaligned_positions=misaligned,
            misaligned_count=len(misaligned),
            alerts=alerts,
            suggested_actions=suggested_actions,
            current_regime=current_regime,
            holdings_count=len(holdings),
        )

    def _infer_sector(self, symbol: str) -> str:
        """Best-effort sector inference from symbol name."""
        s = symbol.upper()
        if any(x in s for x in ["BANK", "HDFC", "ICICI", "KOTAK", "AXIS", "SBI", "BAJFIN"]):
            return "Financials"
        if any(x in s for x in ["TCS", "INFY", "WIPRO", "HCLT", "TECH"]):
            return "IT"
        if any(x in s for x in ["RELIANCE", "ONGC", "BPCL", "IOC"]):
            return "Energy"
        if any(x in s for x in ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVIS"]):
            return "Pharma"
        if any(x in s for x in ["BTC", "ETH", "SOL", "BNB", "XRP"]):
            return "Crypto"
        if any(x in s for x in ["AAPL", "MSFT", "GOOGL", "NVDA", "META", "AMZN"]):
            return "US Tech"
        if any(x in s for x in ["GC=F", "SI=F", "GOLD"]):
            return "Commodities"
        return "Other"

    def _empty_xray(self, regime: str = None) -> PortfolioXRay:
        return PortfolioXRay(
            total_value=0, long_value=0, short_value=0, net_exposure_pct=0,
            top5_concentration=0, largest_position_pct=0, largest_position="",
            sector_breakdown={}, most_crowded_sector="", most_crowded_pct=0,
            estimated_annual_vol=0, estimated_10pct_loss=0, regime_fit_score=1.0,
            misaligned_positions=[], misaligned_count=0,
            alerts=[PositionAlert("PORTFOLIO","low","concentration","No holdings found","Add positions to analyze")],
            suggested_actions=["No holdings to analyze"],
            current_regime=regime, holdings_count=0,
        )


# Module-level singleton
xray_engine = PortfolioXRayEngine()
