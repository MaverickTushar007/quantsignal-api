
# QuantSignal MCP Server

Use QuantSignal live trading signals directly inside Claude Desktop, Cursor, or any MCP-compatible AI tool.

## Quick Setup

### 1. Download the MCP server script
```bash
curl -O https://raw.githubusercontent.com/MaverickTushar007/quantsignal-api/master/quantsignal-mcp.py
pip install mcp httpx
```

### 2. Add to Claude Desktop config
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "quantsignal": {
      "command": "python3",
      "args": ["/path/to/quantsignal-mcp.py"]
    }
  }
}
```

### 3. Restart Claude Desktop and ask:
- *"What's the signal for Reliance today?"*
- *"Show me all BUY signals for Indian stocks"*
- *"What's the market mood right now?"*
- *"Search for bitcoin signal"*

## Available Tools

| Tool | Description |
|------|-------------|
| `get_signal` | Signal for any asset (BTC-USD, RELIANCE.NS, AAPL, etc.) |
| `market_summary` | Overall market mood across all 186 assets |
| `get_signals` | Filter by type (CRYPTO, IN_STOCK, STOCK) or direction (BUY/SELL/HOLD) |
| `search_asset` | Find assets by name |

## Live API
Base URL: `https://web-production-1a093.up.railway.app/api/v1/mcp`

- GET `/mcp/signal/{symbol}` — single asset signal
- GET `/mcp/signals?type=IN_STOCK&direction=BUY` — filtered signals  
- GET `/mcp/market-summary` — market mood
- GET `/mcp/search?q=reliance` — search

## Coverage
186 assets: 98 Indian NSE stocks · 31 US stocks · 20 crypto · 13 ETFs · 11 indices · 7 forex · 6 commodities

Built with FastAPI + XGBoost + LightGBM. Signals refresh daily at 6 AM IST.
# Sat Apr 18 04:29:30 IST 2026
