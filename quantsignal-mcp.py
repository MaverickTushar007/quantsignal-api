#!/usr/bin/env python3
"""
QuantSignal MCP Server
Connects Claude Desktop directly to live trading signals.
"""
import asyncio, json, httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

API_BASE = "https://web-production-1a093.up.railway.app/api/v1/mcp"
app = Server("quantsignal")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_signal",
            description="Get ML trading signal (BUY/SELL/HOLD) for any asset — crypto, Indian stocks (NSE), US stocks, forex, commodities. Examples: BTC-USD, RELIANCE.NS, AAPL, EURUSD=X",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Asset symbol e.g. BTC-USD, RELIANCE.NS, AAPL, NIFTY50, GC=F"
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="market_summary",
            description="Get overall market mood — % bullish/bearish across all 186 assets, top BUY and SELL signals",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="get_signals",
            description="Get trading signals filtered by asset type or direction. Types: CRYPTO, IN_STOCK, STOCK, ETF, INDEX, FOREX, COMMODITY. Directions: BUY, SELL, HOLD",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Asset type: CRYPTO, IN_STOCK, STOCK, ETF, INDEX, FOREX, COMMODITY"},
                    "direction": {"type": "string", "description": "Signal direction: BUY, SELL, HOLD"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"}
                }
            }
        ),
        types.Tool(
            name="search_asset",
            description="Search for an asset by name. E.g. 'reliance', 'bitcoin', 'apple', 'nifty'",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Asset name or partial symbol to search"}
                },
                "required": ["query"]
            }
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            if name == "get_signal":
                symbol = arguments["symbol"]
                r = await client.get(f"{API_BASE}/signal/{symbol}")
                data = r.json()
                if "error" in data:
                    text = f"Symbol '{symbol}' not found. Try searching with search_asset tool."
                else:
                    text = (
                        f"**{data['name']} ({data['symbol']})**\n"
                        f"Signal: {data['signal']} | Probability: {data['probability']} | Confidence: {data['confidence']}\n"
                        f"Price: {data['price']} | Take Profit: {data['take_profit']} | Stop Loss: {data['stop_loss']}\n"
                        f"Kelly Size: {data['kelly_size']} | Risk/Reward: {data['risk_reward']}\n"
                        f"\nSummary: {data['summary']}"
                    )

            elif name == "market_summary":
                r = await client.get(f"{API_BASE}/market-summary")
                data = r.json()
                buys = "\n".join([f"  • {s['name']} ({s['symbol']}): {s['probability']}" for s in data['top_buys']])
                sells = "\n".join([f"  • {s['name']} ({s['symbol']}): {s['probability']}" for s in data['top_sells']])
                text = (
                    f"**Market Mood: {data['market_mood']}**\n"
                    f"{data['summary']}\n\n"
                    f"**Top BUY signals:**\n{buys}\n\n"
                    f"**Top SELL signals:**\n{sells}"
                )

            elif name == "get_signals":
                params = {}
                if "type" in arguments: params["type"] = arguments["type"]
                if "direction" in arguments: params["direction"] = arguments["direction"]
                params["limit"] = arguments.get("limit", 10)
                r = await client.get(f"{API_BASE}/signals", params=params)
                data = r.json()
                lines = [f"**{data['total']} signals found:**"]
                for s in data['signals']:
                    lines.append(f"  • {s['name']} ({s['symbol']}): {s['signal']} | {s['probability']} | {s['price']}")
                text = "\n".join(lines)

            elif name == "search_asset":
                r = await client.get(f"{API_BASE}/search", params={"q": arguments["query"]})
                data = r.json()
                if not data['results']:
                    text = f"No assets found for '{arguments['query']}'"
                else:
                    lines = [f"**Found {data['count']} assets for '{data['query']}':**"]
                    for s in data['results']:
                        lines.append(f"  • {s['name']} ({s['symbol']}): {s['signal']} | {s['price']}")
                    text = "\n".join(lines)
            else:
                text = f"Unknown tool: {name}"

        except Exception as e:
            text = f"Error calling QuantSignal API: {e}"

    return [types.TextContent(type="text", text=text)]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
