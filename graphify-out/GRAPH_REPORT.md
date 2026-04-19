# Graph Report - /Users/tusharbhatt/Desktop/quantsignal-api  (2026-04-18)

## Corpus Check
- 128 files · ~55,485 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 756 nodes · 1180 edges · 59 communities detected
- Extraction: 72% EXTRACTED · 28% INFERRED · 0% AMBIGUOUS · INFERRED: 328 edges (avg confidence: 0.76)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]

## God Nodes (most connected - your core abstractions)
1. `generate_signal()` - 30 edges
2. `_get_redis()` - 20 edges
3. `stream_chat()` - 18 edges
4. `_rebuild()` - 17 edges
5. `run()` - 16 edges
6. `enrich_signal()` - 15 edges
7. `get_signal()` - 14 edges
8. `predict()` - 12 edges
9. `fetch_ohlcv()` - 12 edges
10. `_load_prefs()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `clear_cache()` --calls--> `_get_redis()`  [INFERRED]
  /Users/tusharbhatt/Desktop/quantsignal-api/app/api/routes/sentiment.py → /Users/tusharbhatt/Desktop/quantsignal-api/app/infrastructure/cache/cache.py
- `load_shock_cache()` --calls--> `generate_signal()`  [INFERRED]
  /Users/tusharbhatt/Desktop/quantsignal-api/app/domain/data/correlations.py → /Users/tusharbhatt/Desktop/quantsignal-api/app/domain/signal/service.py
- `main()` --calls--> `run()`  [INFERRED]
  /Users/tusharbhatt/Desktop/quantsignal-api/quantsignal-mcp.py → /Users/tusharbhatt/Desktop/quantsignal-api/scripts/regime_evaluate.py
- `build_cache()` --calls--> `generate_signal()`  [INFERRED]
  /Users/tusharbhatt/Desktop/quantsignal-api/cache_signals.py → /Users/tusharbhatt/Desktop/quantsignal-api/app/domain/signal/service.py
- `HealthResponse` --calls--> `health()`  [INFERRED]
  /Users/tusharbhatt/Desktop/quantsignal-api/app/api/schemas.py → /Users/tusharbhatt/Desktop/quantsignal-api/app/api/routes/routes.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.03
Nodes (73): Auto-calibration — runs weekly, updates Platt scaling params in DB. Can be trigg, Fetch closed signals, fit Platt scaling, save to DB.     Returns summary dict., run_calibration(), build_index(), scripts/build_rag_index.py Embeds the foundational quantitative research corpus, _bias(), detect_regime(), Market regime detection — determines if market is trending up, down, or ranging. (+65 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (65): ml/auto_retrain.py Auto-retrains models with win rate below threshold. Called af, Score a model's win rate on recent data. Returns -1 if can't score., Force retrain a model by deleting its pkl and regenerating., Main entry point — score all models, retrain weak ones.     Returns summary dict, retrain_model(), run_auto_retrain(), score_model(), build_cache() (+57 more)

### Community 2 - "Community 2"
Cohesion: 0.04
Nodes (57): get_cached(), _get_redis(), invalidate(), core/cache.py Redis caching layer using Upstash REST API. TTL: 1 hour for signal, set_cached(), load_shock_cache(), Scans all signals for large price moves.     Returns {symbol: shock_warning} for, save_shock_cache() (+49 more)

### Community 3 - "Community 3"
Cohesion: 0.04
Nodes (56): AgentCreate, AgentUpdate, create_agent(), delete_agent(), get_agent_trades(), get_agents(), get_latest_agent_runs(), api/agents.py Virtual Agent Paper Trading — CRUD + executor logic. (+48 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (40): chat_endpoint(), ChatMessage, ChatRequest, generic_chat_endpoint(), api/chat.py Streaming chat endpoint for the Agent Workspace. Yields status updat, format_insider_for_prompt(), _get_cik(), get_insider_trades() (+32 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (33): create_subscription(), get_billing_status(), _plan_tier(), api/routes/billing.py — Razorpay Subscriptions billing layer., razorpay_webhook(), _rzp(), _sb(), _upsert() (+25 more)

### Community 6 - "Community 6"
Cohesion: 0.08
Nodes (32): AlertSubscribe, AlertUnsubscribe, fire_signal_alerts(), get_subscriptions(), api/alerts.py Signal alert subscriptions — users subscribe to assets, get emaile, Send subscription confirmation email., Send signal change alert email., Subscribe email to signal alerts for given symbols. (+24 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (31): explain_replay(), ExplainRequest, api/ai_explain.py AI replay explanation using Groq — fast, free, server-side., BaseModel, Enum, GuardianRequest, api/guardian.py Trade Guardian — personalized risk check before entering a trade, analyze_portfolio() (+23 more)

### Community 8 - "Community 8"
Cohesion: 0.12
Nodes (27): get_conflict_map(), agents/conflict_agent.py ConflictAgent — scans all signals every cron cycle. Det, High severity = high-confidence signal that conflicts strongly., Return latest conflict data keyed by symbol.     Used by Perseus context builder, Full conflict scan across the market.     Returns conflict map + market stress s, run(), _severity(), _store() (+19 more)

### Community 9 - "Community 9"
Cohesion: 0.09
Nodes (25): _detect_conflict(), _generate_interpretation(), generate_signal_context(), _get_regime_stats(), _get_symbol_history(), Signal Context Generator — generates reasoning text for every signal. Stores con, Generate and store context for a signal.     Returns context dict. Never raises., Generate 2-sentence signal interpretation.     Tries: Groq → OpenRouter → templa (+17 more)

### Community 10 - "Community 10"
Cohesion: 0.11
Nodes (22): fetch_calendar(), domain/data/calendar_data.py Standalone calendar fetch — avoids circular import, Return cached calendar events. Falls back to route-level cache if available., debug_calendar(), debug_remind(), fetch_calendar(), get_calendar_events(), get_playbook() (+14 more)

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (21): compute_ev(), get_all_ev_summary(), get_ev_stats(), Expected Value Calculator — replaces static regime multipliers. Computes EV per, Returns EV info for a given regime+direction.     {"ev": float|None, "win_rate":, Master gate: should this signal fire given EV + probability?     Returns (should, Returns human-readable EV summary for all regime+direction pairs., Returns EV stats per (regime, direction) from signal_history.     Cached for 60 (+13 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (18): _activate(), check_circuit_breaker(), get_breaker_status(), _get_recent_outcomes(), Circuit Breaker — pauses signal alerts when system is losing consistently. Check, Returns {"active": bool, "reason": str, "resume_at": str|None}     Fails open (r, Fetch recent evaluated signals from DB., _reset() (+10 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (12): get_fear_greed(), Returns Fear & Greed Index data.     Score: 0 = Extreme Fear, 100 = Extreme Gree, get_funding_features(), get_macro_features(), _load_cache(), Returns dict of macro features for ML model.     Falls back to neutral values if, _save_cache(), get_positioning() (+4 more)

### Community 14 - "Community 14"
Cohesion: 0.3
Nodes (11): get_portfolio(), build_equity_curve(), compute_dual_portfolio(), compute_portfolio(), compute_return(), compute_streaks(), cumulative_pnl(), filter_signals() (+3 more)

### Community 15 - "Community 15"
Cohesion: 0.21
Nodes (10): fetch_earnings_dates(), get_earnings_flag(), Fetch earnings dates for a list of symbols. Returns {symbol: date_str or None}, Rebuild full earnings cache for all stock tickers (skip crypto/forex/commodity)., Returns earnings warning dict if earnings within WARN_DAYS, else None.     {days, rebuild_earnings_cache(), _get_catalyst_and_news(), agents/news_agent.py — live yfinance news + earnings flags for Perseus. (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.33
Nodes (10): _close_hit_positions(), _get(), _patch(), _post(), _process_agent_new_trades(), api/agent_executor.py Virtual Agent Executor — runs every cron cycle. Scans sign, For one agent: find matching signals and open virtual trades., Check all open positions — close if TP or SL hit. (+2 more)

### Community 17 - "Community 17"
Cohesion: 0.24
Nodes (10): admin_dashboard(), admin_signals(), admin_weekly_reports(), api/routes/admin.py Admin dashboard — system health, error patterns, signal qual, Signal quality breakdown per symbol., List all generated weekly reports., Nuclear cache clear — wipes JSON file + all Redis signal keys., Full system health overview. (+2 more)

### Community 18 - "Community 18"
Cohesion: 0.24
Nodes (9): calibrate(), calibrate_probability(), _interpret(), load_calibration_params(), Platt scaling calibration — loads coefficients from DB and applies sigmoid trans, Load latest Platt scaling params from DB. Cached in memory., Apply Platt scaling: sigmoid(coef * raw_prob + intercept).     Falls back to raw, Bucket signals by probability, compute actual win rate per bucket.     A well-ca (+1 more)

### Community 19 - "Community 19"
Cohesion: 0.31
Nodes (8): _evaluate_outcome(), agents/outcome_agent.py OutcomeAgent — checks Guardian alert history and records, Store individual outcome in guardian_outcomes table., Check Guardian alerts from 24h and 72h ago.     Compare predicted direction vs a, Check if the predicted direction was correct by comparing price at alert vs now., run(), _store_outcome(), _store_run()

### Community 20 - "Community 20"
Cohesion: 0.36
Nodes (7): get_threshold(), agents/calibration_agent.py CalibrationAgent — runs weekly (or on-demand). Reads, Get calibrated (prob_threshold, ev_minimum) for a symbol.     Falls back to defa, Full calibration cycle.     1. Load all guardian_outcomes     2. Compute per-sym, run(), _store(), _store_run()

### Community 21 - "Community 21"
Cohesion: 0.36
Nodes (7): BacktestResult, _load_bundle(), ml/backtest.py Walk-forward backtester using actual trained production models. N, Load the production model bundle for this ticker., Backtest using the production model — walk forward through     historical data,, run(), Trade

### Community 22 - "Community 22"
Cohesion: 0.4
Nodes (2): core/config.py All settings loaded from .env file. Import settings from here — n, Settings

### Community 23 - "Community 23"
Cohesion: 0.4
Nodes (3): get_current_user(), api/auth.py JWT auth via Supabase. get_current_user — validates token, returns u, V1: Currently returning a mock pro user for everyone so the app is usable.     V

### Community 24 - "Community 24"
Cohesion: 0.5
Nodes (4): agents/briefing_agent.py BriefingAgent — synthesizes all agent outputs into a mo, Generate morning briefing from all agent outputs., run(), _store()

### Community 25 - "Community 25"
Cohesion: 0.5
Nodes (4): agents/risk_agent.py RiskAgent — monitors for dangerous signal patterns and port, Analyze recent signals for risk patterns.     Returns risk assessment — never ra, run(), _store()

### Community 26 - "Community 26"
Cohesion: 0.5
Nodes (3): analyze_event_impact(), compute_atr(), backtest_events.py Measures real price impact of NFP/FOMC/CPI on your asset univ

### Community 27 - "Community 27"
Cohesion: 0.5
Nodes (1): main.py FastAPI application entry point. Run with: python -m uvicorn main:app --

### Community 28 - "Community 28"
Cohesion: 0.67
Nodes (3): get_current_price(), Fetch current price from cache or return a default., websocket_prices()

### Community 29 - "Community 29"
Cohesion: 0.67
Nodes (3): chunk_text(), ingest(), core/rag_ingest.py Chunks quant research papers and stores embeddings in Supabas

### Community 30 - "Community 30"
Cohesion: 0.67
Nodes (3): fit_platt(), Refit Platt scaling calibration from closed signals in DB. Run this whenever you, sigmoid()

### Community 31 - "Community 31"
Cohesion: 0.67
Nodes (1): Recalculate all stored probabilities using latest calibration params + regime mu

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (0): 

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): data/universe.py Single source of truth for all 86 assets. Every other module im

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): Fetch Nifty 50 + S&P 500 benchmark returns and save to data/benchmark_cache.json

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (0): 

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (0): 

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (0): 

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (0): 

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (0): 

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (0): 

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (0): 

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (0): 

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (0): 

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (0): 

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (0): 

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (0): 

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (0): 

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (0): 

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (0): 

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (0): 

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (0): 

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (0): 

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (0): 

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (0): 

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (0): 

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (0): 

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **216 isolated node(s):** `Pre-compute all 86 signals + LLM reasoning locally, saving to a JSON cache file.`, `main.py FastAPI application entry point. Run with: python -m uvicorn main:app --`, `core/config.py All settings loaded from .env file. Import settings from here — n`, `api/schemas.py Pydantic models — every API response is validated against these.`, `api/liquidity.py Real-time liquidity levels — OI change, funding trend, liquidat` (+211 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 32`** (2 nodes): `root()`, `main_test.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (2 nodes): `data/universe.py Single source of truth for all 86 assets. Every other module im`, `universe.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (2 nodes): `Fetch Nifty 50 + S&P 500 benchmark returns and save to data/benchmark_cache.json`, `refresh_benchmark.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `main.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `stream_chat()` connect `Community 4` to `Community 0`, `Community 5`, `Community 8`, `Community 10`, `Community 13`?**
  _High betweenness centrality (0.144) - this node is a cross-community bridge._
- **Why does `generate_signal()` connect `Community 1` to `Community 2`, `Community 3`, `Community 4`, `Community 6`, `Community 7`, `Community 8`, `Community 9`, `Community 10`, `Community 15`?**
  _High betweenness centrality (0.144) - this node is a cross-community bridge._
- **Why does `get_signal()` connect `Community 6` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 7`, `Community 8`, `Community 9`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Are the 71 inferred relationships involving `str` (e.g. with `get_liquidity_levels()` and `subscribe_alerts()`) actually correct?**
  _`str` has 71 INFERRED edges - model-reasoned connections that need verification._
- **Are the 25 inferred relationships involving `generate_signal()` (e.g. with `build_cache()` and `watchlist_signals()`) actually correct?**
  _`generate_signal()` has 25 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `_get_redis()` (e.g. with `protection_middleware()` and `_get_all_reasoning_states()`) actually correct?**
  _`_get_redis()` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 15 inferred relationships involving `stream_chat()` (e.g. with `chat_endpoint()` and `generic_chat_endpoint()`) actually correct?**
  _`stream_chat()` has 15 INFERRED edges - model-reasoned connections that need verification._