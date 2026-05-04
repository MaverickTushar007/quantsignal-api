from __future__ import annotations
import json, os, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR     = Path(__file__).resolve().parents[3]
RESULTS_FILE = BASE_DIR / "data" / "news_backtest_results.json"
RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

_finbert_pipe = None

def _load_finbert():
    global _finbert_pipe
    if _finbert_pipe is not None:
        return _finbert_pipe
    try:
        from transformers import pipeline
        _finbert_pipe = pipeline("text-classification", model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert", truncation=True, max_length=512)
        print("[news_backtest] FinBERT loaded")
        return _finbert_pipe
    except Exception as e:
        print(f"[news_backtest] FinBERT not available ({e}), using word-count fallback")
        return None

BULL_WORDS = {"surge","rally","gain","rise","jump","soar","beat","record","outperform","bullish","upgrade","buy","strong","growth","profit","breakout","momentum","milestone","exceeded","positive"}
BEAR_WORDS = {"crash","drop","fall","decline","plunge","miss","loss","bearish","downgrade","sell","weak","warning","risk","cut","concern","lawsuit","investigation","default","recession","layoff"}

def _wordcount_sentiment(text: str) -> tuple[str, float]:
    words = text.lower().split()
    clean = [w.strip(".,!?;:'\"()[]") for w in words]
    bulls = sum(1 for w in clean if w in BULL_WORDS)
    bears = sum(1 for w in clean if w in BEAR_WORDS)
    total = bulls + bears
    if total == 0:
        return "NEUTRAL", 0.5
    if bulls > bears:
        return "BULLISH", bulls / total
    if bears > bulls:
        return "BEARISH", bears / total
    return "NEUTRAL", 0.5

def score_sentiment(text: str) -> tuple[str, float]:
    pipe = _load_finbert()
    if pipe is not None:
        try:
            result = pipe(text[:512])[0]
            label_map = {"positive": "BULLISH", "negative": "BEARISH", "neutral": "NEUTRAL"}
            label = label_map.get(result["label"].lower(), "NEUTRAL")
            return label, float(result["score"])
        except Exception:
            pass
    return _wordcount_sentiment(text)

def _fetch_price_at(symbol: str, ts: datetime, horizon_hours: int) -> Optional[float]:
    try:
        import yfinance as yf
        target = ts + timedelta(hours=horizon_hours)
        start  = target - timedelta(hours=4)
        end    = target + timedelta(hours=4)
        interval = "1h" if horizon_hours <= 24 else "1d"
        df = yf.Ticker(symbol).history(start=start, end=end, interval=interval)
        if df.empty:
            return None
        df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
        closest = df.iloc[(df.index - target).abs().argmin()]
        return float(closest["Close"])
    except Exception:
        return None

def _fetch_base_price(symbol: str, ts: datetime) -> Optional[float]:
    return _fetch_price_at(symbol, ts, 0)

HORIZONS = {"1h": 1, "4h": 4, "24h": 24, "5d": 5 * 24}

def run_news_backtest(symbol: str, news_items: list, force_refresh: bool = False) -> list[dict]:
    results = _load_results()
    new_results = []
    for item in news_items:
        key = f"{symbol}::{item.title[:80]}"
        if key in results and not force_refresh:
            continue
        label, confidence = score_sentiment(f"{item.title} {item.summary or ''}")
        ts = None
        try:
            raw_ts = getattr(item, "published_at", None) or getattr(item, "date", None)
            if isinstance(raw_ts, (int, float)):
                ts = datetime.fromtimestamp(raw_ts)
            elif isinstance(raw_ts, str):
                ts = datetime.fromisoformat(raw_ts.replace("Z", ""))
        except Exception:
            pass
        if ts is None:
            ts = datetime.utcnow() - timedelta(days=1)
        p0 = _fetch_base_price(symbol, ts)
        if p0 is None or p0 == 0:
            continue
        horizon_results = {}
        for label_str, hours in HORIZONS.items():
            p_future = _fetch_price_at(symbol, ts, hours)
            if p_future is None:
                continue
            ret = (p_future - p0) / p0
            direction = "UP" if ret > 0 else "DOWN"
            correct = (
                (label == "BULLISH" and direction == "UP") or
                (label == "BEARISH" and direction == "DOWN")
            )
            horizon_results[label_str] = {
                "return": round(ret * 100, 4),
                "direction": direction,
                "correct": correct,
            }
        record = {
            "symbol": symbol, "title": item.title, "source": item.source,
            "sentiment": label, "confidence": round(confidence, 4),
            "ts": ts.isoformat(), "horizons": horizon_results,
        }
        results[key] = record
        new_results.append(record)
        time.sleep(0.3)
    _save_results(results)
    print(f"[news_backtest] {symbol}: {len(new_results)} new records saved")
    return list(results.values())

def source_accuracy(source: str, horizon: str = "24h") -> float:
    results = _load_results()
    relevant = [r for r in results.values()
        if r.get("source") == source and horizon in r.get("horizons", {})
        and r["sentiment"] != "NEUTRAL"]
    if len(relevant) < 5:
        return 0.5
    correct = sum(1 for r in relevant if r["horizons"][horizon].get("correct", False))
    return round(correct / len(relevant), 4)

def signal_weight(source: str, sentiment: str, horizon: str = "24h") -> float:
    if sentiment == "NEUTRAL":
        return 1.0
    acc = source_accuracy(source, horizon)
    return round(min(max(0.5 + acc, 0.5), 1.5), 4)

def get_backtest_summary() -> dict:
    results = list(_load_results().values())
    if not results:
        return {"total": 0, "message": "No backtest data yet."}
    total = len(results)
    by_horizon = {}
    for horizon in HORIZONS:
        relevant = [r for r in results if horizon in r.get("horizons", {}) and r["sentiment"] != "NEUTRAL"]
        if not relevant:
            continue
        correct = sum(1 for r in relevant if r["horizons"][horizon].get("correct", False))
        avg_ret_correct = [r["horizons"][horizon]["return"] for r in relevant if r["horizons"][horizon].get("correct")]
        avg_ret_wrong   = [r["horizons"][horizon]["return"] for r in relevant if not r["horizons"][horizon].get("correct")]
        by_horizon[horizon] = {
            "total": len(relevant), "correct": correct,
            "accuracy_pct": round(correct / len(relevant) * 100, 2),
            "avg_return_correct": round(sum(avg_ret_correct) / len(avg_ret_correct), 4) if avg_ret_correct else 0,
            "avg_return_wrong":   round(sum(avg_ret_wrong)   / len(avg_ret_wrong),   4) if avg_ret_wrong   else 0,
        }
    sources = {}
    for r in results:
        src = r.get("source", "unknown")
        if src not in sources:
            sources[src] = {"total": 0, "correct_24h": 0}
        sources[src]["total"] += 1
        if "24h" in r.get("horizons", {}) and r["horizons"]["24h"].get("correct"):
            sources[src]["correct_24h"] += 1
    source_stats = {
        src: {"total": v["total"], "accuracy_24h": round(v["correct_24h"] / v["total"] * 100, 1) if v["total"] else 0}
        for src, v in sources.items() if v["total"] >= 3
    }
    return {"total": total, "by_horizon": by_horizon, "by_source": source_stats}

def _load_results() -> dict:
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_results(results: dict):
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)
