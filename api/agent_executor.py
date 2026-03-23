"""
api/agent_executor.py
Virtual Agent Executor — runs every cron cycle.
Scans signals, opens trades, closes positions, enforces kill switch.
"""
import os, json, urllib.request
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

STRATEGY_FILTERS = {
    "india":  lambda s: s.endswith(".NS") or s.endswith(".BO"),
    "crypto": lambda s: s.endswith("-USD"),
    "us":     lambda s: not s.endswith(".NS") and not s.endswith("-USD"),
    "all":    lambda s: True,
}

# ── Supabase helpers ──────────────────────────────────────────────────────────

def _get(table, params=""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=HEADERS)
    return json.loads(urllib.request.urlopen(req).read())

def _post(table, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=data, headers=HEADERS, method="POST")
    result = json.loads(urllib.request.urlopen(req).read())
    return result[0] if isinstance(result, list) else result

def _patch(table, row_id, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
        data=data, headers={**HEADERS, "Prefer": "return=minimal"},
        method="PATCH")
    urllib.request.urlopen(req)

# ── Core executor ─────────────────────────────────────────────────────────────

def run_agent_executor():
    """Main function — called from cron every cycle."""
    from core.signal_service import generate_signal
    import yfinance as yf

    print("=== Agent Executor Starting ===")

    # 1. Get all active agents
    agents = _get("agents", "status=eq.active")
    print(f"Active agents: {len(agents)}")

    # 2. Check paused agents — resume if pause period expired
    paused = _get("agents", "status=eq.paused")
    now = datetime.now(timezone.utc)
    for agent in paused:
        paused_until = agent.get("paused_until")
        if paused_until:
            try:
                resume_time = datetime.fromisoformat(
                    paused_until.replace("Z", "+00:00"))
                if now >= resume_time:
                    _patch("agents", agent["id"], {
                        "status": "active",
                        "consecutive_losses": 0,
                        "paused_until": None
                    })
                    print(f"Agent '{agent['name']}' resumed after pause")
                    agents.append({**agent, "status": "active",
                                   "consecutive_losses": 0})
            except Exception:
                pass

    if not agents:
        print("No active agents — skipping")
        return

    # 3. Get all current signals from cache
    try:
        signals_raw = json.loads(
            open("data/signals_cache.json").read())
    except Exception:
        print("No signals cache found — skipping")
        return

    # 4. For each agent, scan signals and open new trades
    for agent in agents:
        try:
            _process_agent_new_trades(agent, signals_raw)
        except Exception as e:
            print(f"Error processing agent '{agent['name']}': {e}")

    # 5. Close open positions where TP/SL has been hit
    _close_hit_positions()

    print("=== Agent Executor Done ===")


def _process_agent_new_trades(agent, signals_raw):
    """For one agent: find matching signals and open virtual trades."""
    agent_id = agent["id"]
    strategy  = agent.get("strategy", "all")
    min_prob  = agent.get("min_probability", 0.65)
    budget    = agent.get("budget_inr", 100000)

    sym_filter = STRATEGY_FILTERS.get(strategy, STRATEGY_FILTERS["all"])

    # Get already open positions for this agent
    open_trades = _get("agent_trades",
        f"agent_id=eq.{agent_id}&outcome=eq.open")
    open_symbols = {t["symbol"] for t in open_trades}

    # Rough available capital (10% per trade max, track open positions)
    max_per_trade = budget * 0.10   # 10% of budget per trade
    allocated = sum(t.get("invested_inr", 0) for t in open_trades)
    available = budget - allocated
    if available < max_per_trade * 0.5:
        return  # Not enough capital

    new_trades = 0

    for symbol, sig in signals_raw.items():
        if not sym_filter(symbol):
            continue
        if symbol in open_symbols:
            continue   # Already in this position
        if not isinstance(sig, dict):
            continue

        direction   = sig.get("direction", "HOLD")
        probability = sig.get("probability", 0)
        entry       = sig.get("entry_price") or sig.get("entry", 0)
        tp          = sig.get("take_profit", 0)
        sl          = sig.get("stop_loss", 0)

        if direction == "HOLD":
            continue
        if probability < min_prob:
            continue
        if not entry or not tp or not sl:
            continue

        # Calculate position size (INR)
        invest_inr = min(max_per_trade, available * 0.33)
        if invest_inr < 500:
            continue

        # For INR-denominated stocks convert USD price if needed
        shares = invest_inr / entry if entry > 0 else 0

        _post("agent_trades", {
            "agent_id": agent_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(entry, 4),
            "take_profit": round(tp, 4),
            "stop_loss":   round(sl, 4),
            "probability": round(probability, 4),
            "shares":      round(shares, 4),
            "invested_inr": round(invest_inr, 2),
            "outcome": "open",
        })

        available -= invest_inr
        open_symbols.add(symbol)
        new_trades += 1

        if new_trades >= 5:   # Max 5 new trades per cycle per agent
            break

    if new_trades:
        print(f"  Agent '{agent['name']}': opened {new_trades} trades")


def _close_hit_positions():
    """Check all open positions — close if TP or SL hit."""
    import yfinance as yf

    open_trades = _get("agent_trades", "outcome=eq.open")
    if not open_trades:
        return

    # Batch fetch current prices
    symbols = list({t["symbol"] for t in open_trades})
    prices = {}
    for sym in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            prices[sym] = fi.last_price
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()
    agents_to_update = {}  # agent_id -> {pnl_delta, outcome}

    for trade in open_trades:
        sym     = trade["symbol"]
        price   = prices.get(sym)
        if not price:
            continue

        direction  = trade["direction"]
        tp         = trade["take_profit"]
        sl         = trade["stop_loss"]
        entry      = trade["entry_price"]
        invest_inr = trade.get("invested_inr", 0)
        agent_id   = trade["agent_id"]

        hit = None
        if direction == "BUY":
            if price >= tp:
                hit = "TP_HIT"
            elif price <= sl:
                hit = "SL_HIT"
        elif direction == "SELL":
            if price <= tp:
                hit = "TP_HIT"
            elif price >= sl:
                hit = "SL_HIT"

        if not hit:
            # Auto-expire trades older than 5 days
            try:
                opened = datetime.fromisoformat(
                    trade["opened_at"].replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - opened).days >= 5:
                    hit = "EXPIRED"
            except Exception:
                pass

        if hit:
            pnl_pct = 0.0
            if entry > 0:
                raw = (price - entry) / entry
                pnl_pct = raw if direction == "BUY" else -raw
            pnl_inr = round(invest_inr * pnl_pct, 2)

            _patch("agent_trades", trade["id"], {
                "outcome":   hit,
                "closed_at": now_iso,
                "pnl_pct":   round(pnl_pct * 100, 2),
                "pnl_inr":   pnl_inr,
            })

            # Track for agent-level update
            if agent_id not in agents_to_update:
                agents_to_update[agent_id] = {"pnl": 0, "trades": 0,
                                               "losses": 0, "wins": 0}
            agents_to_update[agent_id]["pnl"]    += pnl_inr
            agents_to_update[agent_id]["trades"] += 1
            if hit == "SL_HIT":
                agents_to_update[agent_id]["losses"] += 1
            elif hit == "TP_HIT":
                agents_to_update[agent_id]["wins"] += 1

    # Update agent-level stats + kill switch
    for agent_id, stats in agents_to_update.items():
        try:
            agent_row = _get("agents", f"id=eq.{agent_id}")
            if not agent_row:
                continue
            agent = agent_row[0]

            new_pnl    = agent.get("total_pnl_inr", 0) + stats["pnl"]
            new_trades = agent.get("total_trades", 0)  + stats["trades"]
            consec     = agent.get("consecutive_losses", 0)

            if stats["wins"] > 0:
                consec = 0   # reset on any win
            consec += stats["losses"]

            update = {
                "total_pnl_inr":      round(new_pnl, 2),
                "total_trades":       new_trades,
                "consecutive_losses": consec,
            }

            # Kill switch — 5 consecutive losses → pause 48h
            if consec >= 5:
                resume = datetime.now(timezone.utc) + timedelta(hours=48)
                update["status"]       = "paused"
                update["paused_until"] = resume.isoformat()
                print(f"⚠ Kill switch triggered for agent {agent_id} "
                      f"— paused 48h")

            _patch("agents", agent_id, update)

        except Exception as e:
            print(f"Agent update error {agent_id}: {e}")
