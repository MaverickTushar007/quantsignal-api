"""
domain/portfolio/stress.py
W3.2 — Scenario stress simulation.
Estimates portfolio loss under named macro scenarios.
Uses position vol + beta estimates — no future data.
"""
import logging
from typing import List, Dict

log = logging.getLogger(__name__)

# Scenario definitions: name → per-asset-class shock (% loss)
SCENARIOS: Dict[str, Dict[str, float]] = {
    "bear_regime": {
        "crypto":       -0.45,
        "us_tech":      -0.30,
        "india_large":  -0.20,
        "india_mid":    -0.28,
        "gold":         +0.05,
        "default":      -0.22,
    },
    "rate_hike": {
        "crypto":       -0.25,
        "us_tech":      -0.18,
        "india_large":  -0.12,
        "india_mid":    -0.15,
        "gold":         -0.08,
        "default":      -0.12,
    },
    "tech_drawdown": {
        "crypto":       -0.35,
        "us_tech":      -0.40,
        "india_large":  -0.10,
        "india_mid":    -0.12,
        "gold":         +0.03,
        "default":      -0.10,
    },
    "vol_spike": {
        "crypto":       -0.30,
        "us_tech":      -0.20,
        "india_large":  -0.15,
        "india_mid":    -0.20,
        "gold":         +0.08,
        "default":      -0.15,
    },
    "india_macro_shock": {
        "crypto":       -0.10,
        "us_tech":      -0.05,
        "india_large":  -0.18,
        "india_mid":    -0.25,
        "gold":         +0.06,
        "default":      -0.18,
    },
}


def run_stress(
    holdings: List[Dict],
    total_value: float,
    scenarios: List[str] | None = None,
) -> Dict[str, Dict]:
    """
    Run stress scenarios on a portfolio.
    Returns dict of scenario_name → {loss_abs, loss_pct, per_position}.
    Never raises — returns empty dict on failure.
    """
    if not holdings or total_value <= 0:
        return {}

    active = scenarios or list(SCENARIOS.keys())
    results = {}

    for name in active:
        shocks = SCENARIOS.get(name)
        if not shocks:
            continue
        try:
            total_loss = 0.0
            per_position = []
            for h in holdings:
                val        = float(h.get("value", 0))
                asset_cls  = h.get("asset_class", "default")
                side       = h.get("side", "LONG")
                shock      = shocks.get(asset_cls, shocks["default"])
                # SHORT positions gain when market falls
                direction_mult = -1.0 if side == "SHORT" else 1.0
                pos_loss = val * shock * direction_mult
                total_loss += pos_loss
                per_position.append({
                    "symbol":    h.get("symbol", "?"),
                    "shock_pct": round(shock * 100, 1),
                    "loss_abs":  round(pos_loss, 2),
                })
            results[name] = {
                "loss_abs":     round(total_loss, 2),
                "loss_pct":     round(total_loss / total_value * 100, 2),
                "per_position": per_position,
                "description":  _describe(name, total_loss, total_value),
            }
        except Exception as e:
            log.warning(f"[stress] scenario {name} failed: {e}")

    return results


def _describe(name: str, loss: float, total: float) -> str:
    pct = abs(loss / total * 100)
    severity = "severe" if pct > 25 else "moderate" if pct > 12 else "mild"
    labels = {
        "bear_regime":        "broad market bear regime",
        "rate_hike":          "aggressive central bank rate hike",
        "tech_drawdown":      "technology sector drawdown",
        "vol_spike":          "volatility spike (VIX-style shock)",
        "india_macro_shock":  "India-specific macro shock",
    }
    label = labels.get(name, name)
    direction = "loss" if loss < 0 else "gain"
    return f"{severity.capitalize()} {direction} of {pct:.1f}% under {label}"
