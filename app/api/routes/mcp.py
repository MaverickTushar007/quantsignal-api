"""
api/mcp.py
MCP (Model Context Protocol) endpoint — makes QuantSignal usable
inside Claude, Cursor, Windsurf, and any AI tool that supports MCP.
Users can ask their AI: "What's the signal on Reliance today?"
"""
from fastapi import APIRouter
from pathlib import Path
import json

router = APIRouter()

@router.get("/mcp/manifest", tags=["mcp"])
def mcp_manifest():
    """MCP tool manifest — tells AI tools what QuantSignal can do."""
    return {
        "schema_version": "v1",
        "name_for_human": "QuantSignal",
        "name_for_model": "quantsignal",
        "description_for_human": "ML-powered trading signals for 186 assets — crypto, Indian stocks, US stocks, forex, commodities.",
        "description_for_model": "Get ML trading signals (BUY/SELL/HOLD) with probability scores, confidence levels, price targets, and stop losses for 186 assets including Indian NSE stocks, US stocks, crypto, forex, and commodities. Signals are refreshed daily.",
        "auth": {"type": "none"},
        "api": {
            "type": "openapi",
            "url": "https://web-production-1a093.up.railway.app/api/v1/mcp/openapi.json",
        },
        "logo_url": "https://quantsignal-web.vercel.app/icon-192.png",
        "contact_email": "support@quantsignal.app",
        "legal_info_url": "https://quantsignal-web.vercel.app/landing",
    }

@router.get("/mcp/openapi.json", tags=["mcp"])
def mcp_openapi():
    """OpenAPI spec for MCP tools."""
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "QuantSignal API",
            "version": "1.0.0",
            "description": "ML trading signals for 186 assets",
        },
        "servers": [{"url": "https://web-production-1a093.up.railway.app/api/v1"}],
        "paths": {
            "/mcp/signal/{symbol}": {
                "get": {
                    "operationId": "getSignal",
                    "summary": "Get trading signal for a specific asset",
                    "description": "Returns BUY/SELL/HOLD signal with probability, confidence, price targets and stop loss for any supported asset.",
                    "parameters": [{
                        "name": "symbol",
                        "in": "path",
                        "required": True,
                        "description": "Asset symbol. Examples: BTC-USD, RELIANCE.NS, AAPL, EURUSD=X, GC=F",
                        "schema": {"type": "string"}
                    }],
                    "responses": {
                        "200": {"description": "Signal data"},
                        "404": {"description": "Symbol not found"},
                    }
                }
            },
            "/mcp/signals": {
                "get": {
                    "operationId": "getAllSignals",
                    "summary": "Get all trading signals",
                    "description": "Returns signals for all 186 assets. Filter by type: CRYPTO, IN_STOCK, STOCK, ETF, INDEX, FOREX, COMMODITY",
                    "parameters": [
                        {
                            "name": "type",
                            "in": "query",
                            "required": False,
                            "description": "Filter by asset type: CRYPTO, IN_STOCK, STOCK, ETF, INDEX, FOREX, COMMODITY",
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "direction",
                            "in": "query", 
                            "required": False,
                            "description": "Filter by signal direction: BUY, SELL, HOLD",
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "description": "Max number of results (default 20)",
                            "schema": {"type": "integer"}
                        }
                    ],
                    "responses": {"200": {"description": "List of signals"}}
                }
            },
            "/mcp/market-summary": {
                "get": {
                    "operationId": "getMarketSummary",
                    "summary": "Get overall market mood and top signals",
                    "description": "Returns market-wide summary: % bullish/bearish, top BUY signals, top SELL signals, strongest confidence picks.",
                    "responses": {"200": {"description": "Market summary"}}
                }
            },
            "/mcp/search": {
                "get": {
                    "operationId": "searchAssets",
                    "summary": "Search for an asset by name or symbol",
                    "description": "Find assets by partial name or symbol. E.g. 'reliance', 'bitcoin', 'apple'",
                    "parameters": [{
                        "name": "q",
                        "in": "query",
                        "required": True,
                        "description": "Search query — asset name or symbol",
                        "schema": {"type": "string"}
                    }],
                    "responses": {"200": {"description": "Matching assets"}}
                }
            }
        }
    }

@router.get("/mcp/signal/{symbol}", tags=["mcp"])
def get_signal(symbol: str):
    """Get full signal for a specific symbol."""
    try:
        cache = json.loads(Path("data/signals_cache.json").read_text())
        
        # Try exact match first
        sig = cache.get(symbol)
        
        # Try case-insensitive + common variations
        if not sig:
            sym_upper = symbol.upper()
            for k, v in cache.items():
                if k.upper() == sym_upper:
                    sig = v
                    break
        
        if not sig:
            # Search by display name
            sym_lower = symbol.lower()
            for k, v in cache.items():
                if sym_lower in v.get("name", "").lower() or sym_lower in v.get("display", "").lower():
                    sig = v
                    break

        if not sig:
            return {"error": f"Symbol '{symbol}' not found. Use /mcp/search?q={symbol} to find the right symbol."}

        currency = "₹" if sig.get("type") == "IN_STOCK" else "$"
        
        return {
            "symbol": sig["symbol"],
            "name": sig["name"],
            "type": sig["type"],
            "signal": sig["direction"],
            "probability": f"{sig['probability']:.0%}",
            "confidence": sig["confidence"],
            "price": f"{currency}{sig['current_price']:,.2f}",
            "take_profit": f"{currency}{sig.get('take_profit', 0):,.2f}",
            "stop_loss": f"{currency}{sig.get('stop_loss', 0):,.2f}",
            "kelly_size": f"{sig.get('kelly_size', 0):.1%} of portfolio",
            "risk_reward": sig.get("risk_reward", 0),
            "summary": f"{sig['name']} is showing a {sig['direction']} signal with {sig['probability']:.0%} probability and {sig['confidence']} confidence at {currency}{sig['current_price']:,.2f}.",
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/mcp/signals", tags=["mcp"])
def get_signals(type: str = None, direction: str = None, limit: int = 20):
    """Get filtered signals."""
    try:
        cache = json.loads(Path("data/signals_cache.json").read_text())
        from app.domain.data.universe import TICKERS
        ticker_map = {t["symbol"]: t for t in TICKERS}
        
        results = []
        for sym, sig in cache.items():
            if type and sig.get("type", "").upper() != type.upper():
                continue
            if direction and sig.get("direction", "").upper() != direction.upper():
                continue
            
            currency = "₹" if sig.get("type") == "IN_STOCK" else "$"
            results.append({
                "symbol": sym,
                "name": sig["name"],
                "type": sig["type"],
                "signal": sig["direction"],
                "probability": f"{sig['probability']:.0%}",
                "confidence": sig["confidence"],
                "price": f"{currency}{sig['current_price']:,.2f}",
            })
        
        return {
            "total": len(results),
            "signals": results[:limit],
            "note": f"Showing {min(limit, len(results))} of {len(results)} signals. Use ?type=CRYPTO or ?direction=BUY to filter."
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/mcp/market-summary", tags=["mcp"])
def market_summary():
    """Overall market mood."""
    try:
        cache = json.loads(Path("data/signals_cache.json").read_text())
        
        total = len(cache)
        buys = [s for s in cache.values() if s["direction"] == "BUY"]
        sells = [s for s in cache.values() if s["direction"] == "SELL"]
        holds = [s for s in cache.values() if s["direction"] == "HOLD"]
        
        # Top picks by probability
        top_buys = sorted(buys, key=lambda x: x["probability"], reverse=True)[:5]
        top_sells = sorted(sells, key=lambda x: x["probability"], reverse=True)[:5]
        
        mood = "BULLISH" if len(buys) > len(sells) * 1.5 else "BEARISH" if len(sells) > len(buys) * 1.5 else "NEUTRAL"
        
        return {
            "market_mood": mood,
            "summary": f"{len(buys)}/{total} assets bullish ({len(buys)/total:.0%}), {len(sells)}/{total} bearish ({len(sells)/total:.0%}), {len(holds)}/{total} neutral",
            "bullish_pct": f"{len(buys)/total:.0%}",
            "bearish_pct": f"{len(sells)/total:.0%}",
            "neutral_pct": f"{len(holds)/total:.0%}",
            "top_buys": [{"symbol": s["symbol"], "name": s["name"], "probability": f"{s['probability']:.0%}"} for s in top_buys],
            "top_sells": [{"symbol": s["symbol"], "name": s["name"], "probability": f"{s['probability']:.0%}"} for s in top_sells],
            "total_assets": total,
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/mcp/search", tags=["mcp"])
def search_assets(q: str):
    """Search assets by name or symbol."""
    try:
        cache = json.loads(Path("data/signals_cache.json").read_text())
        q_lower = q.lower()
        
        matches = []
        for sym, sig in cache.items():
            if (q_lower in sym.lower() or 
                q_lower in sig.get("name", "").lower() or
                q_lower in sig.get("display", "").lower()):
                currency = "₹" if sig.get("type") == "IN_STOCK" else "$"
                matches.append({
                    "symbol": sym,
                    "name": sig["name"],
                    "type": sig["type"],
                    "signal": sig["direction"],
                    "price": f"{currency}{sig['current_price']:,.2f}",
                })
        
        return {
            "query": q,
            "results": matches[:10],
            "count": len(matches),
        }
    except Exception as e:
        return {"error": str(e)}
