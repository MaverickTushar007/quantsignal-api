from app.core.config import BASE_DIR
import json
import asyncio
from pathlib import Path
import groq
from groq import AsyncGroq
from app.core.config import settings


def _build_agent_context(symbol: str, user_id: str) -> str:
    """
    Query specialist agents and return a formatted context block
    for injection into Perseus system prompt.
    Never raises — returns empty string on failure.
    """
    try:
        import os
        from supabase import create_client
        sb = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        )

        lines = ["## LIVE AGENT INTELLIGENCE"]

        # Latest signal context for this symbol
        if symbol and symbol != "GENERIC":
            try:
                res = sb.table("signal_context")                     .select("direction,ev_score,energy_state,context_text,conflict_detected,generated_at")                     .eq("symbol", symbol)                     .order("generated_at", desc=True).limit(1).execute()
                if res.data:
                    sc = res.data[0]
                    lines.append(f"### Signal Context — {symbol}")
                    lines.append(f"- Direction: {sc.get('direction')} | EV: {sc.get('ev_score', 'N/A')} | Energy: {sc.get('energy_state', 'N/A')}")
                    if sc.get("context_text"):
                        lines.append(f"- Interpretation: {sc['context_text'][:200]}")
                    if sc.get("conflict_detected"):
                        lines.append(f"- ⚠️ CONFLICT DETECTED: {sc.get('conflict_reason', 'signal conflict')}")
            except Exception:
                pass

        # Latest RiskAgent run
        try:
            res = sb.table("agent_runs").select("findings,run_at")                 .eq("agent", "RiskAgent")                 .order("run_at", desc=True).limit(1).execute()
            if res.data:
                f = res.data[0].get("findings", {})
                lines.append(f"### RiskAgent — {f.get('risk_level', 'unknown').upper()} risk")
                if f.get("warnings"):
                    for w in f["warnings"]:
                        lines.append(f"- ⚠️ {w}")
                if f.get("circuit_breaker"):
                    lines.append("- 🚨 CIRCUIT BREAKER ACTIVE — reduce position sizes")
        except Exception:
            pass

        # Latest RegimeAgent run
        try:
            res = sb.table("agent_runs").select("findings,run_at")                 .eq("agent", "RegimeAgent")                 .order("run_at", desc=True).limit(1).execute()
            if res.data:
                f   = res.data[0].get("findings", {})
                sym_regime = f.get("regime_map", {}).get(symbol, "unknown")
                sym_energy = f.get("energy_map", {}).get(symbol, "unknown")
                alerts     = f.get("alerts", [])
                lines.append(f"### RegimeAgent — {symbol} regime: {sym_regime} | energy: {sym_energy}")
                if alerts:
                    lines.append(f"- {len(alerts)} high-conviction alert(s) across market")
                    for a in alerts[:2]:
                        lines.append(f"  • {a['symbol']} {a['direction']}: {a.get('reason', '')}")
        except Exception:
            pass

        # Latest BriefingAgent commentary
        try:
            res = sb.table("agent_runs").select("findings,run_at")                 .eq("agent", "BriefingAgent")                 .order("run_at", desc=True).limit(1).execute()
            if res.data:
                f = res.data[0].get("findings", {})
                if f.get("commentary"):
                    lines.append(f"### Morning Briefing")
                    lines.append(f.get("commentary", "")[:300])
        except Exception:
            pass

        # ConflictAgent — ML vs regime/energy conflicts
        try:
            from app.domain.agents.conflict_agent import get_conflict_map
            cmap = get_conflict_map()
            if symbol in cmap:
                c = cmap[symbol]
                lines.append(f"### ConflictAgent — Signal Conflict Detected")
                lines.append(f"- {symbol} has ML vs regime/energy conflict (severity: {c.get('severity','?')})")
                for r in c.get('reasons', []):
                    lines.append(f"  - {r}")
                lines.append(f"- ⚠️ Trade with caution — conflicting signals reduce edge")
            else:
                lines.append(f"### ConflictAgent")
                lines.append(f"- No ML/regime/energy conflict detected for {symbol} — signals aligned")
        except Exception:
            pass

        # NewsAgent — live headlines + catalysts
        try:
            res = sb.table("agent_runs").select("findings,run_at")                 .eq("agent", "NewsAgent")                 .order("run_at", desc=True).limit(1).execute()
            if res.data:
                f = res.data[0].get("findings", {})
                # Live headlines for this symbol
                sym_headlines = f.get("headlines", {}).get(symbol, [])
                sym_catalyst  = f.get("catalysts", {}).get(symbol, {})
                lines.append(f"### NewsAgent — Live Headlines for {symbol}")
                if sym_headlines:
                    for h in sym_headlines[:3]:
                        lines.append(f"- {h}")
                else:
                    lines.append(f"- No recent headlines found for {symbol}")
                if sym_catalyst:
                    risk = sym_catalyst.get("risk", "medium").upper()
                    note = sym_catalyst.get("note", "")
                    lines.append(f"- ⚠️ CATALYST [{risk}]: {note}")
                if f.get("high_risk"):
                    lines.append(f"- Market-wide HIGH RISK symbols: {', '.join(f['high_risk'][:5])}")
        except Exception:
            pass

        # User preferences
        try:
            from app.api.routes.preferences import _load_prefs
            prefs = _load_prefs(user_id)
            watchlist = prefs.get("watchlist", [])
            risk_tol  = prefs.get("risk_tolerance", "medium")
            lines.append(f"### User Profile")
            lines.append(f"- Risk tolerance: {risk_tol}")
            if watchlist:
                lines.append(f"- Watchlist: {', '.join(watchlist[:5])}")
        except Exception:
            pass

        return "\n".join(lines) if len(lines) > 1 else ""

    except Exception as e:
        return ""


def _groq_reasoning(prompt: str) -> str:
    client = groq.Groq(api_key=settings.groq_api_key)
    for model in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip()
        except groq.RateLimitError:
            continue
    raise RuntimeError("All Groq models rate-limited")


def _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features):
    pct = f"{probability*100:.0f}%"
    feat_str = ", ".join(top_features[:3]) if top_features else "technical indicators"
    return (f"ML ensemble signals {direction} with {pct} confidence for {ticker}. "
            f"Primary drivers: {feat_str}. {confluence_bulls}/9 confluence factors align.")


def _compute_conviction(probability: float, confluence_score: str, model_agreement: float = 0) -> dict:
    """
    Hard rules layer — conviction band based on quantitative thresholds.
    LLM must use this, not invent its own.
    """
    # Parse confluence score e.g. "6/9 bullish" -> 6
    conf_bulls = 0
    try:
        conf_bulls = int(str(confluence_score).split("/")[0])
    except Exception:
        pass

    prob_pct = probability * 100
    agreement_pct = model_agreement * 100

    # Hard conviction rules
    if prob_pct >= 72 and conf_bulls >= 6 and agreement_pct >= 70:
        conviction = "HIGH"
        tradable = True
        note = f"Strong edge: {prob_pct:.0f}% probability, {conf_bulls}/9 confluence, {agreement_pct:.0f}% model agreement"
    elif prob_pct >= 60 and conf_bulls >= 4:
        conviction = "MODERATE"
        tradable = True
        note = f"Moderate edge: {prob_pct:.0f}% probability, {conf_bulls}/9 confluence factors aligned"
    elif prob_pct >= 50 and conf_bulls >= 3:
        conviction = "LOW"
        tradable = False
        note = f"Weak edge: {prob_pct:.0f}% probability only — watchlist, do not size up"
    else:
        conviction = "INSUFFICIENT"
        tradable = False
        note = f"No tradable edge: {prob_pct:.0f}% probability, {conf_bulls}/9 confluence — skip or wait"

    # Regime conflict penalty
    if prob_pct < 65 and conf_bulls < 4:
        note += " | ⚠️ Low confluence — signal may be noise"

    return {"conviction": conviction, "tradable": tradable, "note": note}


def get_reasoning(ticker, name, direction, probability, confluence_bulls,
                  top_features, news_headlines, current_price=0,
                  take_profit=0, stop_loss=0, atr=0, volume_ratio=1.0, model_agreement=0):
    headlines_str = "\n".join(f"- {h}" for h in news_headlines[:3]) or "No recent news."
    feat_str = ", ".join(top_features[:4]) if top_features else "momentum and trend"
    confidence_label = "HIGH" if probability >= 0.72 else "MEDIUM" if probability >= 0.58 else "LOW"
    prompt = f"""You are a quantitative analyst writing a trade note. Be specific, direct, and market-aware.
Do NOT use generic phrases like "potential reversal due to volatility" or "not financial advice".
Write exactly 3 sentences. Each sentence must contain specific numbers from the data below.

SIGNAL DATA:
- Asset: {name} ({ticker})
- Direction: {direction} | ML Confidence: {probability*100:.1f}% ({confidence_label}) | Model Agreement: {model_agreement*100:.0f}%
- Current Price: {current_price} | Take Profit: {take_profit} | Stop Loss: {stop_loss}
- ATR: {atr} | Confluence: {confluence_bulls}/9 bullish factors
- Top ML drivers: {feat_str}
- Recent news:
{headlines_str}

FORMAT:
Sentence 1: What the ML model sees technically RIGHT NOW (cite top 2 drivers with signal direction).
Sentence 2: Risk/reward at these specific price levels (use actual TP and SL numbers).
Sentence 3: The single biggest risk that would invalidate this signal (specific to this asset).

Do not start with "The" or "This". Start with the asset name or a verb."""
    try:
        return _groq_reasoning(prompt)
    except Exception:
        return _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features)


async def stream_chat(symbol: str, message: str, history: list, user_id: str = "default", mode: str = "auto"):
    def _yield_status(msg: str):
        return f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

    try:
        if symbol == "GENERIC":
            yield _yield_status("Initializing FinSight Global Intelligence...")
        else:
            yield _yield_status(f"Initializing FinSight workspace for {symbol}...")
        await asyncio.sleep(0.4)

        sig_data = None
        if symbol != "GENERIC":
            yield _yield_status(f"Syncing real-time indicators for {symbol}...")
            cache_path = BASE_DIR / "data/signals_cache.json"
            if cache_path.exists():
                cache = json.loads(cache_path.read_text())
                sig_data = cache.get(symbol)
        else:
            yield _yield_status("Scanning global markets and macro sentiment...")
        await asyncio.sleep(0.4)

        yield _yield_status("Accessing quantitative research corpus (RAG)...")
        rag_text = "No academic context available."
        _rag_mode = "quant" if mode == "quant" else "auto"
        try:
            from app.domain.reasoning.rag import search_research
            if sig_data:
                feat = ", ".join(sig_data.get("top_features", []))
                dir_ = sig_data.get("direction", "neutral")
                qs = f"{dir_} signal {feat} momentum volatility technicals"
                chunks = search_research(qs, top_k=2)
                rag_text = "\n".join([f"- {c}" for c in chunks])
            elif symbol == "GENERIC":
                chunks = search_research(message, top_k=2)
                rag_text = "\n".join([f"- {c}" for c in chunks])
        except Exception:
            pass

        macro_context = ""
        funding_context = ""
        try:
            from app.domain.data.macro import get_macro_features
            macro = get_macro_features()
            macro_context = (
                f"LIVE MACRO REGIME:\n"
                f"- Fed Funds Rate: {macro.get('fed_funds_rate', 'N/A')}%\n"
                f"- CPI YoY: {macro.get('cpi_yoy', 'N/A')}%\n"
                f"- VIX: {macro.get('vix', 'N/A')} ({'HIGH FEAR' if macro.get('high_fear') else 'NORMAL'})\n"
                f"- Yield Spread 10Y-2Y: {macro.get('yield_spread_10y2y', 'N/A')}\n"
                f"- Recession Signal: {'YES' if macro.get('recession_signal') else 'NO'}\n"
                f"- Rate Hike Regime: {'YES' if macro.get('rate_hike_regime') else 'NO'}\n"
            )
        except Exception:
            pass

        if symbol != "GENERIC":
            try:
                from app.domain.data.funding import get_funding_features
                funding = get_funding_features(symbol)
                if funding.get('funding_rate', 0) != 0:
                    funding_context = (
                        f"FUTURES POSITIONING:\n"
                        f"- Funding Rate: {funding.get('funding_rate', 0):.4f}%\n"
                        f"- Market Positioning: {'OVERLEVERAGED LONGS — bearish contrarian' if funding.get('is_overleveraged_long') else 'OVERLEVERAGED SHORTS — bullish contrarian' if funding.get('is_overleveraged_short') else 'NEUTRAL positioning'}\n"
                    )
            except Exception:
                pass

        yield _yield_status("Searching live market intelligence...")
        await asyncio.sleep(0.3)
        yield _yield_status("Synthesizing hedge fund grade analysis...")

        simple_triggers = ["good buy", "should i", "worth it", "what do you think",
                          "explain", "simple", "layman", "beginner", "understand",
                          "what is", "how does", "good stock", "invest", "safe"]
        expert_triggers = ["rsi", "macd", "confluence", "probability", "atr", "kelly",
                          "divergence", "fibonacci", "bollinger", "stochastic", "regime"]
        msg_lower = message.lower()
        is_simple = any(t in msg_lower for t in simple_triggers)
        is_expert = any(t in msg_lower for t in expert_triggers)
        use_simple_mode = is_simple and not is_expert
        # Explicit mode override takes priority over auto-detection
        if mode == "simple":
            use_simple_mode = True
        elif mode == "quant":
            use_simple_mode = False

        fundamentals_context = ""
        if sig_data and symbol != "GENERIC":
            try:
                from app.domain.data.ownership import get_ownership_context, format_ownership_for_prompt
                ownership = get_ownership_context(symbol)
                if ownership:
                    fundamentals_context = format_ownership_for_prompt(symbol, ownership)
                    try:
                        from app.domain.data.insider import format_insider_for_prompt
                        insider_ctx = format_insider_for_prompt(symbol)
                        if insider_ctx:
                            fundamentals_context += f"\n\n{insider_ctx}"
                    except Exception:
                        pass
            except Exception:
                pass

        direction_lock = ""  # initialized here so it's always in scope
        if use_simple_mode:
            sys_prompt = (
                "You are Perseus, a friendly financial advisor who explains markets in simple, plain English.\n"
                "- Use simple analogies and everyday language\n"
                "- Always end with: 'Want me to go deeper into the technical analysis?'\n"
                "- NOT financial advice — always mention this briefly at the end\n"
                "\nAT THE END:\n---\n🤖 **PERSEUS VERDICT**\n"
                "**Action:** [BUY / SELL / HOLD / WAIT]\n"
                "**Conviction:** [HIGH/MEDIUM/LOW] — one sentence why\n"
                "**Bottom line:** one plain English sentence\n"
            )
        else:
            sys_prompt = (
                "You are Perseus, a quantitative strategist trained on Goldman Sachs research methodology.\n"
                "You apply Derman's volatility regime framework (sticky strike vs sticky delta), "
                "variance risk premium analysis, Kelly-optimal position sizing, and cross-asset correlation regimes.\n"
                "\nRULES:\n"
                "- Classify the current vol regime using Derman's framework — is it sticky strike (trending) or sticky delta (mean-reverting)?\n"
                "- Reference variance risk premium when discussing volatility — implied vs realized vol spread\n"
                "- Use Kelly criterion explicitly for position sizing recommendations\n"
                "- Flag correlation regime — are assets moving together (risk-off) or diverging (regime rotation)?\n"
                "- NEVER use generic phrases like 'the market is volatile' — always quantify with specific numbers\n"
                "- Cite the GS research context provided when relevant\n"
                "- RESPOND in clean Markdown with sections\n"
                "\nAT THE END:\n---\n🤖 **PERSEUS VERDICT**\n"
                "**Action:** [BUY / SELL / HOLD / WAIT FOR CONFIRMATION]\n"
                "**Vol Regime:** [STICKY STRIKE / STICKY DELTA / TRANSITIONING]\n"
                "**Conviction:** [HIGH/MEDIUM/LOW] — one line rationale with specific numbers\n"
                "**Kelly-optimal size:** specific % of portfolio\n"
                "**Entry zone:** specific price or range\n"
                "**Target:** specific price\n"
                "**Stop:** specific price\n"
                "**Primary risk:** one sentence with quantified downside\n"
            )

        if direction_lock:
            sys_prompt += direction_lock

        if fundamentals_context:
            sys_prompt += f"\n## FUNDAMENTAL DATA\n{fundamentals_context}\n"

        if sig_data:
            # Hard conviction rules — LLM cannot override these
            conviction_data = _compute_conviction(
                sig_data.get("probability", 0),
                sig_data.get("confluence_score", "0/9"),
                sig_data.get("model_agreement", 0)
            )
            sys_prompt += f"\n## LIVE SIGNAL DATA — {symbol}\n"
            sys_prompt += f"- **ML Bias:** {sig_data.get('direction')} @ {sig_data.get('probability', 0)*100:.1f}% confidence\n"
            sys_prompt += f"- **Confluence:** {sig_data.get('confluence_score', 'N/A')}\n"
            sys_prompt += f"- **Key Drivers:** {', '.join(sig_data.get('top_features', []))}\n"
            sys_prompt += f"- **Entry:** ${sig_data.get('current_price')} | **TP:** ${sig_data.get('take_profit')} | **SL:** ${sig_data.get('stop_loss')}\n"
            sys_prompt += f"- **Kelly Size:** {sig_data.get('kelly_size')}% | **R/R:** {sig_data.get('risk_reward')}:1\n"
            sys_prompt += f"- **Model Agreement:** {sig_data.get('model_agreement', 0)*100:.0f}%\n"
            sys_prompt += f"\n## ⚠️ CONVICTION RULES — YOU MUST USE EXACTLY THIS:\n"
            sys_prompt += f"- **Conviction:** {conviction_data['conviction']} — {conviction_data['note']}\n"
            sys_prompt += f"- **Tradable:** {'YES — size per Kelly' if conviction_data['tradable'] else 'NO — watchlist only, do not recommend entry'}\n"
            if not conviction_data['tradable']:
                sys_prompt += f"- **INSTRUCTION:** Do NOT recommend entry. Signal is weak. Tell user to wait or watch.\n"
        elif symbol == "GENERIC":
            sys_prompt += "\nMODE: Global Macro Intelligence. Cover Stocks, Forex, Crypto, Commodities.\n"

        if macro_context:
            sys_prompt += f"\n## {macro_context}\n"
        if funding_context:
            sys_prompt += f"\n## {funding_context}\n"
        if rag_text and rag_text != "No academic context available.":
            sys_prompt += f"\n## QUANTITATIVE RESEARCH CONTEXT\n{rag_text}\n"

        # Inject memory context
        try:
            from app.domain.core.memory import build_perseus_context
            session_id = f"{symbol}_{user_id}"
            mem_context = build_perseus_context(user_id, symbol, session_id)
            if mem_context:
                sys_prompt += f"\n## MEMORY & CONTEXT\n{mem_context}\n"
        except Exception as _mem_e:
            pass

        # Inject live agent intelligence
        try:
            agent_ctx = _build_agent_context(symbol, user_id)
            if agent_ctx:
                sys_prompt += f"\n{agent_ctx}\n"
        except Exception:
            pass

        sys_prompt += "\nSearch the web for the latest news, price action, and analyst views before responding.\n"

        if not settings.groq_api_key:
            yield _yield_status("Error: No Groq API Key found.")
            return

        # Token-level guard — estimate before sending to Groq
        from app.domain.billing.middleware import get_user_tier
        from app.domain.billing.plans import get_plan
        estimated_tokens = len(message) // 4
        tier = get_user_tier(user_id)
        plan = get_plan(tier)
        token_limit = plan.get("perseus_max_input_tokens", 300 if tier == "free" else 9999)
        if estimated_tokens > token_limit:
            yield f"data: {json.dumps({'type': 'error', 'message': 'token_limit', 'used': estimated_tokens, 'limit': token_limit, 'tier': tier})}\n"
            return

        client = AsyncGroq(api_key=settings.groq_api_key)
        # Cap system prompt to prevent 413 from Groq
        if len(sys_prompt) > 3500:
            sys_prompt = sys_prompt[:3500] + "\n[Context truncated for brevity]"
        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": message})

        PRIMARY_MODEL  = "llama-3.3-70b-versatile"
        FALLBACK_MODEL = "llama-3.1-8b-instant"
        try:
            stream = await client.chat.completions.create(
                model=PRIMARY_MODEL,
                messages=messages,
                stream=True,
                temperature=0.2,
                max_tokens=1200
            )
        except groq.RateLimitError:
            yield "data: " + json.dumps({"type": "status", "message": "Primary model rate-limited — switching to fallback model..."}) + "\n\n"
            stream = await client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=messages,
                stream=True,
                temperature=0.2,
                max_tokens=1200
            )

        full_response = ""
        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

        # Save to conversation history
        try:
            from app.domain.core.memory import save_message, set_user_memory
            session_id = f"{symbol}_{user_id}"
            save_message(user_id, session_id, "user", message, {"symbol": symbol})
            save_message(user_id, session_id, "assistant", full_response[:4000], {"symbol": symbol})
            try:
                set_user_memory(user_id, f"viewed_{symbol}", {"symbol": symbol, "last_asked": message[:100]}, "watchlist")
            except Exception:
                pass
        except Exception:
            pass

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
