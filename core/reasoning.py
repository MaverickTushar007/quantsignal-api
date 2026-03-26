import groq
from core.config import settings


def _groq_reasoning(prompt: str) -> str:
    client = groq.Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _rule_based_reasoning(ticker, direction, probability, confluence_bulls, top_features):
    pct = f"{probability*100:.0f}%"
    feat_str = ", ".join(top_features[:3]) if top_features else "technical indicators"
    return (f"ML ensemble signals {direction} with {pct} confidence for {ticker}. "
            f"Primary drivers: {feat_str}. {confluence_bulls}/9 confluence factors align.")


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


async def stream_chat(symbol: str, message: str, history: list):
    """
    FinSight Assistant SSE generator (Streaming Chat).
    """
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
            cache_path = Path("data/signals_cache.json")
            if cache_path.exists():
                cache = json.loads(cache_path.read_text())
                sig_data = cache.get(symbol)
        else:
            yield _yield_status("Scanning global markets and macro sentiment...")
        await asyncio.sleep(0.4)

        yield _yield_status("Accessing quantitative research corpus (RAG)...")
        rag_text = "No academic context available."
        try:
            from core.rag import search_research
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
            from data.macro import get_macro_features
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
                from data.funding import get_funding_features
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

        fundamentals_context = ""
        if sig_data and symbol != "GENERIC":
            try:
                from data.ownership import get_ownership_context, format_ownership_for_prompt
                ownership = get_ownership_context(symbol)
                if ownership:
                    fundamentals_context = format_ownership_for_prompt(symbol, ownership)
                    try:
                        from data.insider import format_insider_for_prompt
                        insider_ctx = format_insider_for_prompt(symbol)
                        if insider_ctx:
                            fundamentals_context += f"\n\n{insider_ctx}"
                    except Exception:
                        pass
            except Exception:
                pass

        if use_simple_mode:
            sys_prompt = (
                "You are Perseus, a friendly financial advisor who explains markets in simple, plain English.\n"
                "You are talking to someone who may not know financial jargon.\n"
                "\nYOUR STYLE:\n"
                "- Use simple analogies and everyday language\n"
                "- Avoid jargon — if you must use a term, explain it in brackets\n"
                "- Use emojis sparingly to make it friendly 📈\n"
                "- Structure: What is happening → Is it good or bad → What should they know → Bottom line\n"
                "- Always end with: 'Want me to go deeper into the technical analysis?'\n"
                "\nSTRICT RULES:\n"
                "- NEVER use terms like RSI, MACD, ATR, confluence without explaining them\n"
                "- ALWAYS give a clear bottom line in 1-2 sentences\n"
                "- Think like you're explaining to a smart friend who doesn't trade\n"
                "- Still cite real numbers from the data provided\n"
                "- NOT financial advice — always mention this briefly at the end\n"
                "\nAT THE END — always add a verdict block:\n"
                "---\n"
                "🤖 **PERSEUS VERDICT**\n"
                "**Action:** [BUY / SELL / HOLD / WAIT]\n"
                "**Conviction:** [HIGH/MEDIUM/LOW] — one sentence why\n"
                "**Bottom line:** one plain English sentence\n"
            )
        else:
            sys_prompt = (
                "You are Perseus, an elite quantitative analyst at a top-tier hedge fund.\n"
                "You have access to real-time web search. Use it to find current news, analyst reports, and market data.\n"
                "\nYOUR ANALYTICAL FRAMEWORK:\n"
                "1. TECHNICAL LAYER — ML signal confluence, momentum, mean reversion\n"
                "2. MACRO LAYER — Fed policy, inflation regime, yield curve, risk-on/off\n"
                "3. POSITIONING LAYER — funding rates, long/short ratios, options flow\n"
                "4. FUNDAMENTAL LAYER — valuation multiples, growth, balance sheet health\n"
                "5. NEWS CATALYST LAYER — recent events that could move price\n"
                "6. RISK LAYER — ATR-based stops, Kelly sizing, expected value\n"
                "\nSTRICT RULES:\n"
                "- NEVER give basic definitions or generic advice\n"
                "- ALWAYS cite specific numbers from the context provided\n"
                "- ALWAYS identify the primary risk to the thesis\n"
                "- RESPOND in clean Markdown with sections\n"
                "- Think like you're writing a trade note for a senior PM\n"
                "- Use web search to find the LATEST news and analyst views on the asset\n"
                "\nAT THE END — always finish with a verdict block:\n"
                "---\n"
                "🤖 **PERSEUS VERDICT**\n"
                "**Action:** [BUY / SELL / HOLD / WAIT FOR CONFIRMATION]\n"
                "**Conviction:** [HIGH/MEDIUM/LOW] — one line rationale\n"
                "**Entry zone:** specific price or range\n"
                "**Target:** specific price\n"
                "**Stop:** specific price\n"
                "**Primary risk:** one sentence\n"
            )

        if fundamentals_context:
            sys_prompt += f"\n## FUNDAMENTAL DATA\n{fundamentals_context}\n"

        if sig_data:
            sys_prompt += f"\n## LIVE SIGNAL DATA — {symbol}\n"
            sys_prompt += f"- **ML Bias:** {sig_data.get('direction')} @ {sig_data.get('probability', 0)*100:.1f}% confidence\n"
            sys_prompt += f"- **Confluence:** {sig_data.get('confluence_score', 'N/A')}\n"
            sys_prompt += f"- **Key Drivers:** {', '.join(sig_data.get('top_features', []))}\n"
            sys_prompt += f"- **Entry:** ${sig_data.get('current_price')} | **TP:** ${sig_data.get('take_profit')} | **SL:** ${sig_data.get('stop_loss')}\n"
            sys_prompt += f"- **Kelly Size:** {sig_data.get('kelly_size')}% | **R/R:** {sig_data.get('risk_reward')}:1\n"
            sys_prompt += f"- **Model Agreement:** {sig_data.get('model_agreement', 0)*100:.0f}%\n"
        elif symbol == "GENERIC":
            sys_prompt += "\nMODE: Global Macro Intelligence. Cover Stocks, Forex, Crypto, Commodities.\n"

        if macro_context:
            sys_prompt += f"\n## {macro_context}\n"
        if funding_context:
            sys_prompt += f"\n## {funding_context}\n"
        if rag_text and rag_text != "No academic context available.":
            sys_prompt += f"\n## QUANTITATIVE RESEARCH CONTEXT\n{rag_text}\n"

        whatif_keywords = ["what if", "if vix", "if market", "if rate", "if price drops",
                           "if it falls", "if it rises", "scenario", "suppose", "assuming",
                           "what happens if", "if fed", "if inflation", "if i reduce",
                           "if position", "stress test"]
        is_whatif = any(kw in message.lower() for kw in whatif_keywords)

        if is_whatif:
            sys_prompt += (
                "\n## SCENARIO ANALYSIS MODE\n"
                "The user is asking a 'what if' scenario question. Respond with:\n"
                "1. Current baseline\n2. Scenario impact\n3. Trigger levels\n4. Hedge suggestion\n"
                "Be specific with numbers.\n"
            )

        sys_prompt += "\nSearch the web for the latest news, price action, and analyst views before responding.\n"

        if not settings.groq_api_key:
            yield _yield_status("Error: No Groq API Key found.")
            return

        client = AsyncGroq(api_key=settings.groq_api_key)
        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": message})

        try:
            stream = await client.chat.completions.create(
                model="compound-beta",
                messages=messages,
                stream=True,
                temperature=0.2,
                max_tokens=1200
            )
        except Exception:
            stream = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                stream=True,
                temperature=0.2,
                max_tokens=1200
            )

        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
# Thu Mar 26 15:08:43 IST 2026
