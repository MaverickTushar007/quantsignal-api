# quantsignal-api — Architecture

## Stack
- FastAPI (Python)
- PostgreSQL (via infrastructure/db)
- Redis (via infrastructure/cache)
- Deployed on Railway

---

## Directory Map

```
app/
├── main.py                        # App entry — registers all routers
│
├── api/
│   ├── routes/
│   │   ├── routes.py              # 265 lines — core signal endpoints (4 endpoints)
│   │   ├── signals_ext.py         # 262 lines — original extended file (kept for safety)
│   │   ├── market_context.py      # 121 lines — news, mood, backtest, regime
│   │   ├── signal_stream.py       # 150 lines — debug signal + SSE stream
│   │   ├── cron.py                # 252 lines — HTTP cron handlers only (delegates to tasks.py)
│   │   ├── tasks.py               # 263 lines — rebuild/job execution logic
│   │   ├── system.py              # 158 lines — health, ev-stats, morning briefing
│   │   ├── admin.py               # Admin endpoints
│   │   ├── agent_executor.py      # Agent execution endpoints
│   │   ├── agents.py              # Agent management endpoints
│   │   ├── ai_explain.py          # AI explanation endpoints
│   │   ├── alerts.py              # Alert subscription endpoints
│   │   ├── auth.py                # Auth middleware (require_pro)
│   │   ├── billing.py             # Billing endpoints
│   │   ├── calendar.py            # Economic calendar endpoints
│   │   ├── chat.py                # Chat endpoints
│   │   ├── feedback.py            # Feedback endpoints
│   │   ├── guardian.py            # TradeGuardian endpoints
│   │   ├── history.py             # Trade history endpoints
│   │   ├── liquidity.py           # Liquidity level endpoints
│   │   ├── mcp.py                 # MCP endpoints
│   │   ├── metrics.py             # Metrics endpoints
│   │   ├── montecarlo.py          # Monte Carlo simulation endpoints
│   │   ├── payments.py            # Payment/checkout endpoints
│   │   ├── performance.py         # Performance endpoints
│   │   ├── portfolio.py           # Portfolio endpoints
│   │   ├── portfolio_tracker.py   # Portfolio tracker endpoints
│   │   ├── preferences.py         # User preferences endpoints
│   │   ├── replay.py              # Signal replay endpoints
│   │   ├── sentiment.py           # Market sentiment endpoints
│   │   ├── weekly_report.py       # Weekly report endpoints
│   │   └── ws.py                  # WebSocket endpoints
│   │
│   └── schemas.py                 # Pydantic models (MarketMood, BacktestSummary, etc.)
│
├── domain/                        # Business logic — no HTTP here
│   ├── signal/
│   │   └── service.py             # generate_signal() — core signal pipeline
│   ├── regime/
│   │   └── detector.py            # detect_regime()
│   ├── billing/
│   │   └── middleware.py          # signal_gate() — rate limiting
│   ├── agents/                    # Agent domain logic
│   ├── alerts/                    # Alert domain logic
│   ├── core/                      # Core domain utilities
│   ├── data/
│   │   └── universe.py            # TICKERS, TICKER_MAP
│   ├── documents/                 # Document handling
│   ├── ml/                        # ML models
│   ├── performance/               # Performance calculation
│   ├── portfolio/                 # Portfolio logic
│   └── reasoning/                 # AI reasoning logic
│
└── infrastructure/                # External adapters
    ├── cache/
    │   └── cache.py               # get_cached(), set_cached()
    ├── db/                        # Database connections
    ├── documents/                 # Document storage
    ├── queue/                     # Job queue
    └── scheduler/                 # Cron scheduler
```

---

## Request Flow

```
HTTP Request
  → main.py (router registration)
  → api/routes/*.py (thin HTTP handler — validate, call domain, return)
  → domain/*/service.py (business logic)
  → infrastructure/* (cache, db, external APIs)
  → Response
```

---

## Route File Responsibilities

| File | Responsibility | Endpoints |
|---|---|---|
| routes.py | Core signal CRUD | GET /signals, GET /signals/:symbol, POST /signals/generate |
| market_context.py | Market data | GET /news/:symbol, GET /market/mood, GET /backtest/:symbol, GET /regime/:symbol |
| signal_stream.py | Debug + streaming | GET /signals/debug/:symbol, GET /signals/:symbol/stream (SSE) |
| cron.py | HTTP cron triggers | POST /cron/* — delegates all logic to tasks.py |
| tasks.py | Job execution | _rebuild_* functions called by cron.py |
| system.py | System health | GET /health, GET /system/ev-stats, GET /system/morning-briefing |

---

## Rules

1. **Route files are HTTP-only** — validate input, call domain, return response. No business logic.
2. **Domain files have no FastAPI imports** — pure Python business logic only.
3. **Infrastructure files handle external adapters** — cache, db, queues. No business logic.
4. **cron.py delegates to tasks.py** — cron.py only accepts HTTP and triggers. tasks.py does the work.
5. **No file should exceed ~300 lines** — if it does, split by concern.

---

## Line Count Reference (post-refactor)

| File | Lines |
|---|---|
| routes.py | 265 |
| market_context.py | 121 |
| signal_stream.py | 150 |
| signals_ext.py | 262 |
| cron.py | 252 |
| tasks.py | 263 |
| system.py | 158 |
