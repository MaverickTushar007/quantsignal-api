
def _blend_live_performance(symbol: str, ml_probability: float) -> float:
    """
    Blend ML model probability with live signal performance for this symbol.
    Only activates when symbol has 20+ resolved outcomes.
    Blend: 70% ML confidence + 30% live score.
    Live score = 40% win_rate + 35% profit_factor_norm + 25% rr_ratio_norm
    """
    try:
        from app.infrastructure.db.signal_history import _get_conn
        con, db = _get_conn()
        cur = con.cursor()
        ph = "%s" if db == "pg" else "?"

        cur.execute(f"""
            SELECT outcome, entry_price, exit_price, take_profit, stop_loss
            FROM signal_history
            WHERE symbol={ph} AND outcome IN ('win','loss')
            AND exit_price IS NOT NULL AND entry_price > 0
            ORDER BY evaluated_at DESC LIMIT 50
        """, (symbol,))
        rows = cur.fetchall()
        con.close()

        if len(rows) < 5:
            return ml_probability

        wins = [r for r in rows if r[0] == "win"]
        losses = [r for r in rows if r[0] == "loss"]
        n = len(rows)

        win_rate = len(wins) / n

        gross_profit = sum(abs(r[2] - r[1]) for r in wins if r[2] and r[1])
        gross_loss   = sum(abs(r[2] - r[1]) for r in losses if r[2] and r[1])
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 2.0
        pf_norm = min(profit_factor / 3.0, 1.0)  # normalise to 0-1, cap at 3x

        rr_ratios = []
        for r in rows:
            entry, tp, sl = r[1], r[3], r[4]
            if tp and sl and entry and abs(sl - entry) > 0:
                rr_ratios.append(abs(tp - entry) / abs(sl - entry))
        avg_rr = sum(rr_ratios) / len(rr_ratios) if rr_ratios else 1.5
        rr_norm = min(avg_rr / 3.0, 1.0)

        live_score = 0.40 * win_rate + 0.35 * pf_norm + 0.25 * rr_norm

        blended = round(0.70 * ml_probability + 0.30 * live_score, 4)
        return blended

    except Exception:
        return ml_probability
