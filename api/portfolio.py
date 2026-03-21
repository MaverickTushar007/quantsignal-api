"""
api/portfolio.py
Portfolio Lab — optimization, stress test, health score.
Uses Black-Litterman with ML signal probabilities.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from core.config import settings
import numpy as np
import json
from pathlib import Path

router = APIRouter()

class PortfolioAsset(BaseModel):
    symbol: str
    amount: float  # dollar amount

class PortfolioRequest(BaseModel):
    assets: List[PortfolioAsset]
    total_capital: float

# Historical crash scenarios (approximate max drawdowns)
STRESS_SCENARIOS = {
    "2022_crypto_crash": {
        "label": "2022 Crypto Crash",
        "description": "Crypto dropped 65-80%, stocks fell 20-30%",
        "shocks": {
            "CRYPTO": -0.68, "STOCK": -0.22, "INDEX": -0.20,
            "COMMODITY": -0.10, "ETF": -0.18
        }
    },
    "2020_covid": {
        "label": "2020 COVID Crash",
        "description": "Everything sold off 30-40% in 6 weeks",
        "shocks": {
            "CRYPTO": -0.40, "STOCK": -0.35, "INDEX": -0.34,
            "COMMODITY": -0.30, "ETF": -0.32
        }
    },
    "rate_hike_shock": {
        "label": "Rate Hike Shock",
        "description": "Fed aggressively raises rates, growth assets hurt most",
        "shocks": {
            "CRYPTO": -0.45, "STOCK": -0.28, "INDEX": -0.22,
            "COMMODITY": 0.05, "ETF": -0.20
        }
    }
}

def get_asset_type(symbol: str) -> str:
    crypto = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","DOT","LINK","LTC","ATOM","NEAR","OP","INJ","RNDR","FET","ARB","APT","MATIC"]
    if any(symbol.startswith(c) for c in crypto):
        return "CRYPTO"
    if symbol in ["NASDAQ","SP500","RUT","NIKKEI","HANG_SENG","DAX"]:
        return "INDEX"
    if symbol in ["GOLD","OIL","SILVER","NATGAS"]:
        return "COMMODITY"
    if symbol.endswith("ETF") or symbol in ["QQQ","SPY","GLD"]:
        return "ETF"
    return "STOCK"

@router.post("/portfolio/analyze", tags=["portfolio"])
def analyze_portfolio(req: PortfolioRequest):
    if len(req.assets) < 2:
        raise HTTPException(status_code=400, detail="Add at least 2 assets")
    if len(req.assets) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 assets")

    try:
        # Load signal cache for ML probabilities
        cache_path = Path("data/signals_cache.json")
        signal_cache = {}
        if cache_path.exists():
            signal_cache = json.loads(cache_path.read_text())

        # Load price cache for returns/correlation
        price_cache_path = Path("data/price_cache.json")
        price_data = {}
        if price_cache_path.exists():
            price_data = json.loads(price_cache_path.read_text())

        symbols = [a.symbol for a in req.assets]
        amounts = np.array([a.amount for a in req.assets])
        weights = amounts / amounts.sum()

        # Get signal data for each asset
        asset_data = []
        for asset in req.assets:
            sig = signal_cache.get(asset.symbol, {})
            asset_type = get_asset_type(asset.symbol)
            prob = sig.get("probability", 0.5)
            direction = sig.get("direction", "HOLD")
            asset_data.append({
                "symbol": asset.symbol,
                "display": sig.get("display", asset.symbol),
                "amount": asset.amount,
                "weight": asset.amount / req.total_capital,
                "direction": direction,
                "probability": prob,
                "type": asset_type,
                "current_price": sig.get("current_price", 0),
            })

        # ── Correlation matrix (approximate from asset types) ──────
        n = len(symbols)
        corr_matrix = np.eye(n)
        type_correlations = {
            ("CRYPTO", "CRYPTO"): 0.75,
            ("STOCK", "STOCK"): 0.55,
            ("INDEX", "INDEX"): 0.65,
            ("CRYPTO", "STOCK"): 0.25,
            ("CRYPTO", "INDEX"): 0.20,
            ("CRYPTO", "COMMODITY"): -0.05,
            ("STOCK", "INDEX"): 0.80,
            ("STOCK", "COMMODITY"): 0.10,
            ("INDEX", "COMMODITY"): 0.05,
            ("ETF", "STOCK"): 0.85,
            ("ETF", "INDEX"): 0.90,
            ("ETF", "CRYPTO"): 0.30,
        }
        for i in range(n):
            for j in range(i+1, n):
                t1 = asset_data[i]["type"]
                t2 = asset_data[j]["type"]
                corr = type_correlations.get((t1,t2), type_correlations.get((t2,t1), 0.3))
                corr_matrix[i,j] = corr
                corr_matrix[j,i] = corr

        # ── Expected returns using Black-Litterman view ────────────
        # Base market returns (annualized)
        base_returns = {
            "CRYPTO": 0.45, "STOCK": 0.12, "INDEX": 0.10,
            "COMMODITY": 0.06, "ETF": 0.10
        }
        expected_returns = []
        for ad in asset_data:
            base = base_returns.get(ad["type"], 0.10)
            # Blend with ML signal: high BUY prob → higher expected return
            signal_boost = (ad["probability"] - 0.5) * 0.30
            if ad["direction"] == "SELL":
                signal_boost = -(1 - ad["probability"]) * 0.30
            expected_returns.append(base + signal_boost)

        expected_returns = np.array(expected_returns)

        # Annualized volatilities by type
        vols = {
            "CRYPTO": 0.75, "STOCK": 0.28, "INDEX": 0.18,
            "COMMODITY": 0.22, "ETF": 0.20
        }
        vol_array = np.array([vols.get(ad["type"], 0.25) for ad in asset_data])
        cov_matrix = np.outer(vol_array, vol_array) * corr_matrix

        # ── Optimize: maximize Sharpe ratio ───────────────────────
        from scipy.optimize import minimize

        def neg_sharpe(w):
            port_return = np.dot(w, expected_returns)
            port_vol = np.sqrt(w @ cov_matrix @ w)
            return -(port_return - 0.05) / port_vol if port_vol > 0 else 0

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds = [(0.05, 0.60) for _ in range(n)]  # min 5%, max 60% per asset
        result = minimize(neg_sharpe, weights, method="SLSQP",
                         bounds=bounds, constraints=constraints)

        optimal_weights = result.x if result.success else weights
        optimal_weights = np.clip(optimal_weights, 0, 1)
        optimal_weights /= optimal_weights.sum()

        # ── Portfolio metrics ──────────────────────────────────────
        current_return = float(np.dot(weights, expected_returns))
        current_vol = float(np.sqrt(weights @ cov_matrix @ weights))
        current_sharpe = (current_return - 0.05) / current_vol if current_vol > 0 else 0

        opt_return = float(np.dot(optimal_weights, expected_returns))
        opt_vol = float(np.sqrt(optimal_weights @ cov_matrix @ optimal_weights))
        opt_sharpe = (opt_return - 0.05) / opt_vol if opt_vol > 0 else 0

        # ── Stress tests ───────────────────────────────────────────
        stress_results = {}
        for scenario_key, scenario in STRESS_SCENARIOS.items():
            loss = 0
            for i, ad in enumerate(asset_data):
                shock = scenario["shocks"].get(ad["type"], -0.25)
                loss += ad["amount"] * shock
            stress_results[scenario_key] = {
                "label": scenario["label"],
                "description": scenario["description"],
                "total_loss": round(loss, 2),
                "loss_pct": round((loss / req.total_capital) * 100, 1),
                "portfolio_value": round(req.total_capital + loss, 2),
            }

        # ── Health score ───────────────────────────────────────────
        # Diversification: penalize high correlation
        avg_corr = (corr_matrix.sum() - n) / (n * (n-1)) if n > 1 else 0
        div_score = max(0, 100 - avg_corr * 100)

        # Signal alignment: % of portfolio in BUY signals
        buy_weight = sum(ad["weight"] for ad in asset_data if ad["direction"] == "BUY")
        signal_score = buy_weight * 100

        # Concentration: penalize if any asset > 40%
        max_weight = float(weights.max())
        conc_score = max(0, 100 - max(0, max_weight - 0.4) * 200)

        # Volatility score: lower is better
        vol_score = max(0, 100 - current_vol * 80)

        health_score = round(
            div_score * 0.30 + signal_score * 0.30 +
            conc_score * 0.20 + vol_score * 0.20
        )

        if health_score >= 75:
            health_label = "STRONG"
            health_color = "#00ff88"
        elif health_score >= 50:
            health_label = "MODERATE"
            health_color = "#ffd700"
        else:
            health_label = "WEAK"
            health_color = "#ff4466"

        # ── Optimal allocation in dollars ──────────────────────────
        optimal_allocation = []
        for i, ad in enumerate(asset_data):
            opt_amt = round(optimal_weights[i] * req.total_capital, 2)
            curr_amt = ad["amount"]
            change = opt_amt - curr_amt
            optimal_allocation.append({
                "symbol": ad["symbol"],
                "display": ad["display"],
                "current_amount": curr_amt,
                "current_weight": round(float(weights[i]) * 100, 1),
                "optimal_amount": opt_amt,
                "optimal_weight": round(float(optimal_weights[i]) * 100, 1),
                "change": round(change, 2),
                "direction": ad["direction"],
                "probability": round(ad["probability"], 3),
                "type": ad["type"],
            })

        # ── AI Summary ─────────────────────────────────────────────
        ai_summary = ""
        if settings.groq_api_key:
            try:
                import groq
                holdings_str = ", ".join([f"{ad['display']} ({ad['direction']} {ad['probability']*100:.0f}%): ${ad['amount']:,.0f}" for ad in asset_data])
                worst_stress = min(stress_results.values(), key=lambda x: x["total_loss"])

                prompt = f"""You are a portfolio analyst. Give a 3-4 sentence plain English summary of this portfolio. No jargon. Talk like a smart friend.

Holdings: {holdings_str}
Total capital: ${req.total_capital:,.0f}
Health score: {health_score}/100 ({health_label})
Current Sharpe ratio: {current_sharpe:.2f}
Optimized Sharpe ratio: {opt_sharpe:.2f}
Worst stress scenario: {worst_stress['label']} → lose ${abs(worst_stress['total_loss']):,.0f}
Average correlation between assets: {avg_corr:.2f}

Be specific about dollar amounts. End with one clear action."""

                client = groq.Groq(api_key=settings.groq_api_key)
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200, temperature=0.7,
                )
                ai_summary = response.choices[0].message.content.strip()
            except Exception:
                pass

        return {
            "assets": asset_data,
            "total_capital": req.total_capital,
            "current_metrics": {
                "expected_return": round(current_return * 100, 1),
                "volatility": round(current_vol * 100, 1),
                "sharpe_ratio": round(current_sharpe, 2),
            },
            "optimal_metrics": {
                "expected_return": round(opt_return * 100, 1),
                "volatility": round(opt_vol * 100, 1),
                "sharpe_ratio": round(opt_sharpe, 2),
            },
            "optimal_allocation": optimal_allocation,
            "stress_tests": stress_results,
            "health_score": health_score,
            "health_label": health_label,
            "health_color": health_color,
            "diversification_score": round(div_score),
            "signal_alignment": round(signal_score),
            "ai_summary": ai_summary,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
