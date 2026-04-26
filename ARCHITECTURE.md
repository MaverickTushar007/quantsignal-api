# QuantSignal API — Architecture

## Stack
- **Framework:** FastAPI (Python)
- **Deployment:** Railway
- **Cache:** Redis
- **DB:** Supabase (Postgres)
- **ML:** scikit-learn, custom models in `domain/ml/`

---

## Directory Map

```
app/
├── main.py                        # FastAPI app init, all router registration
├── core/
│   └── config.py                  # BASE_DIR, env vars, global config
│
├── api/
│   ├── schemas.py                 # Pydantic models — SignalResponse, WatchlistItem,
│   │                              # MarketMood, BacktestSummary, HealthResponse
│   └── routes/
│       ├── routes.py              # Core signal endpoints (265 lines)
│       │                          # GET /signals, /signals/{symbol},
│       │                          # /signals/{symbol}/reasoning, /health
│       ├── signals_ext.py         # Extended signal endpoints (262 lines)
│       │                          # GET /news, /market/mood, /backtest,
│       │                          # /regime, /debug/regime, /signals/{symbol}/stream
│       ├── cron.py                # Cron HTTP handlers only (252 lines)
│       │                          # POST /cron/refresh, /cron/retrain,
│       │                          # /cron/rebuild-mtf, /cron/guardian, etc.
│       ├── tasks.py               # _rebuild() logic (263 lines)
│       │                          # Full cache rebuild, agent execution,
│       │                          # outcome tracking, shock scan, MTF/earnings cache
│       ├── system.py              # System endpoints (158 lines)
│       │                          # /system/ev-stats, /system/morning-briefing
│       ├── alerts.py              # Alert subscription + Telegram firing
│       ├── agents.py              # AI agent CRUD endpoints
│       ├── agent_executor.py      # Agent execution engine
│       ├── auth.py                # JWT auth, get_current_user, require_pro
│       ├── billing.py             # Subscription management
│       ├── calendar.py            # Economic calendar scraper
│       ├── chat.py                # Perseus chat endpoint
│       ├── feedback.py            # User feedback collection
│       ├── guardian.py            # Trade Guardian check endpoint
│       ├── history.py             # Signal history + trade log
│       ├── liquidity.py           # Liquidation levels data
│       ├── mcp.py                 # MCP server integration
│       ├── metrics.py             # System metrics
│       ├── montecarlo.py          # Monte Carlo simulation endpoint
│       ├── payments.py            # Stripe webhook + checkout
│       ├── performance.py         # Performance stats endpoints
│       ├── portfolio.py           # Portfolio tracking endpoints
│       ├── preferences.py         # User preferences
│       ├── replay.py              # Signal replay endpoint
│       ├── sentiment.py           # Market sentiment endpoint
│       ├── weekly_report.py       # Weekly report generation
│       └── ws.py                  # WebSocket endpoint
│
├── domain/                        # Business logic — no HTTP here
│   ├── signal/
│   │   └── service.py             # generate_signal() — main signal pipeline
│   ├── data/
│   │   ├── universe.py            # TICKERS, TICKER_MAP — all 118 assets
│   │   ├── market.py              # OHLCV fetch, CoinGecko/Yahoo integration
│   │   └── news.py                # News fetch + sentiment
│   ├── ml/
│   │   ├── backtest.py            # Walk-forward backtest runner
│   │   └── ...                    # Model training, calibration
│   ├── reasoning/
│   │   ├── service.py             # get_reasoning() — Perseus AI reasoning
│   │   └── worker.py              # Async reasoning job worker
│   ├── regime/
│   │   └── detector.py            # Market regime detection
│   ├── billing/
│   │   └── middleware.py          # signal_gate — usage limit enforcement
│   ├── agents/                    # AI agent definitions
│   ├── alerts/                    # Alert evaluation logic
│   ├── core/
│   │   └── failure_tracker.py     # Record success/failure per symbol
│   ├── documents/                 # Document processing
│   ├── performance/               # Performance calculation
│   ├── portfolio/                 # Portfolio logic
│   └── reasoning/                 # Reasoning queue + worker
│
└── infrastructure/                # External service adapters
    ├── cache/
    │   └── cache.py               # Redis get/set wrappers
    ├── db/
    │   └── signal_history.py      # Signal history DB reads/writes
    ├── queue/
    │   └── reasoning_queue.py     # Reasoning job queue
    ├── scheduler/                 # Scheduled task runners
    └── documents/                 # Document storage
```

---

## Request Flow

```
HTTP Request
    ↓
main.py (router registration, CORS, middleware)
    ↓
api/routes/*.py  (thin HTTP handlers — validate, auth, call domain)
    ↓
domain/*/        (business logic — signal generation, ML, reasoning)
    ↓
infrastructure/  (Redis cache, Supabase DB, external APIs)
```

## Cron Flow

```
Railway cron / external scheduler
    ↓
POST /cron/refresh  (cron.py — validates CRON_SECRET)
    ↓
tasks._rebuild()    (tasks.py — full pipeline)
    ↓
generate_signal() × 118 assets  (4 parallel workers)
    ↓
Redis cache update + signal history save + alerts fire
```

---

## Key Rules

1. **HTTP handlers stay thin** — routes just validate input, call domain, return response
2. **All business logic in `domain/`** — never put signal generation logic in a route file
3. **Cache-first reads** — always check Redis before hitting external APIs
4. **`tasks.py` owns rebuild** — cron.py just calls `_rebuild()`, never duplicates logic
5. **Auth via `require_pro` / `signal_gate`** — never check billing inline in route logic

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `CRON_SECRET` | Authenticates cron job requests |
| `REDIS_URL` | Redis connection string |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service key |
| `TELEGRAM_BOT_TOKEN` | Telegram alert bot |
| `OPENAI_API_KEY` | Perseus reasoning (if used) |
