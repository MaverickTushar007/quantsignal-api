"""
data/ownership.py
Fetches promoter holding, institutional holding, and
P/E context for Perseus fundamental enrichment.
"""
import yfinance as yf

def get_ownership_context(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        fi = ticker.fast_info

        def safe(key, default=None):
            v = info.get(key)
            return v if v is not None and v != 'N/A' else default

        promoter_pct = safe('heldPercentInsiders')
        institution_pct = safe('heldPercentInstitutions')
        pe = safe('trailingPE')
        forward_pe = safe('forwardPE')

        # Price position in 52w range — proxy for historical cheapness
        current = fi.last_price
        high_52w = fi.year_high
        low_52w = fi.year_low
        year_change = fi.year_change  # e.g. -0.12 = down 12% YoY

        price_percentile = None
        if current and high_52w and low_52w and high_52w != low_52w:
            price_percentile = round(
                max(0.0, min(100.0, (current - low_52w) / (high_52w - low_52w) * 100)), 1
            )

        # P/E context — cheap if price near 52w low
        pe_context = None
        if pe and price_percentile is not None:
            if price_percentile < 25:
                pe_context = "historically cheap — near 52w low"
            elif price_percentile > 75:
                pe_context = "historically expensive — near 52w high"
            else:
                pe_context = "mid-range historically"

        # Promoter holding interpretation
        promoter_signal = None
        if promoter_pct is not None:
            pct = round(promoter_pct * 100, 1)
            sector = safe('sector', '')
            is_bank_or_fin = any(w in sector.lower() for w in ['bank', 'financial', 'insurance'])
            if pct < 2 and is_bank_or_fin:
                promoter_signal = f"{pct}% — Normal for banks/financial firms (no promoter group)"
            elif pct > 65:
                promoter_signal = f"{pct}% — HIGH conviction, founders/promoters holding strongly"
            elif pct > 45:
                promoter_signal = f"{pct}% — MODERATE promoter holding"
            elif pct > 25:
                promoter_signal = f"{pct}% — LOW promoter holding, watch for exits"
            else:
                promoter_signal = f"{pct}% — VERY LOW, promoter conviction weak"

        # Institutional holding interpretation
        inst_signal = None
        if institution_pct is not None:
            pct = round(institution_pct * 100, 1)
            if pct > 30:
                inst_signal = f"{pct}% — Heavy institutional ownership (FII/DII/mutual funds)"
            elif pct > 15:
                inst_signal = f"{pct}% — Moderate institutional presence"
            else:
                inst_signal = f"{pct}% — Low institutional interest"

        return {
            "promoter_holding": promoter_signal,
            "institutional_holding": inst_signal,
            "pe_trailing": round(pe, 1) if pe else None,
            "pe_forward": round(forward_pe, 1) if forward_pe else None,
            "pe_context": pe_context,
            "price_percentile_52w": price_percentile,
            "year_change_pct": round(year_change * 100, 1) if year_change else None,
        }

    except Exception:
        return {}


def format_ownership_for_prompt(symbol: str, data: dict) -> str:
    if not data:
        return ""
    lines = [f"OWNERSHIP & VALUATION CONTEXT for {symbol}:"]

    if data.get("promoter_holding"):
        lines.append(f"  Promoter holding: {data['promoter_holding']}")
    if data.get("institutional_holding"):
        lines.append(f"  Institutional:    {data['institutional_holding']}")
    if data.get("pe_trailing"):
        pe_line = f"  P/E (trailing):   {data['pe_trailing']}"
        if data.get("pe_forward"):
            pe_line += f" | Forward P/E: {data['pe_forward']}"
        if data.get("pe_context"):
            pe_line += f" — {data['pe_context']}"
        lines.append(pe_line)
    if data.get("price_percentile_52w") is not None:
        lines.append(f"  52w position:     {data['price_percentile_52w']}th percentile")
    if data.get("year_change_pct") is not None:
        direction = "up" if data["year_change_pct"] > 0 else "down"
        lines.append(f"  1yr performance:  {direction} {abs(data['year_change_pct'])}%")

    return "\n".join(lines)
