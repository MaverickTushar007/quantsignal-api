from app.core.config import BASE_DIR
"""
api/ws.py
WebSocket endpoint for real-time price updates.
"""
import asyncio
import json
import random
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pathlib import Path

router = APIRouter()

# Active connections: {symbol: [websockets]}
connections = {}

async def get_current_price(symbol: str) -> float:
    """Fetch current price from cache or return a default."""
    cache_path = BASE_DIR / "data/signals_cache.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            if symbol in cache:
                return float(cache[symbol].get("current_price", 0))
        except Exception:
            pass
    return 0.0

@router.websocket("/ws/prices/{symbol}")
async def websocket_prices(websocket: WebSocket, symbol: str):
    await websocket.accept()
    symbol = symbol.upper()
    
    # Get base price
    base_price = await get_current_price(symbol)
    if base_price == 0:
        base_price = 2500.0 if "ETH" in symbol else 65000.0 if "BTC" in symbol else 150.0

    try:
        while True:
            # Simulate price fluctuation +/- 0.05%
            change = base_price * (random.uniform(-0.0005, 0.0005))
            display_price = base_price + change
            
            # Send update
            await websocket.send_json({
                "symbol": symbol,
                "price": round(display_price, 2),
                "timestamp": asyncio.get_event_loop().time()
            })
            
            # Update base price slightly to simulate trend
            base_price = display_price
            
            await asyncio.sleep(2) # Update every 2 seconds
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS Error: {e}")
