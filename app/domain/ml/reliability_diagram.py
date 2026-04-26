"""
domain/ml/reliability_diagram.py
W4.5 — Calibration reliability diagram.
Run: python3 -m app.domain.ml.reliability_diagram
"""
import logging, os
log = logging.getLogger(__name__)

def compute_reliability(bins: int = 10) -> dict:
    try:
        from supabase import create_client
        sb = create_client(
            os.getenv("SUPABASE_URL", ""),
            os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", ""),
        )
        res = (
            sb.table("signals")
            .select("probability,outcome")
            .not_.is_("probability", "null")
            .not_.is_("outcome", "null")
            .execute()
        )
        rows = res.data or []
        if len(rows) < 20:
            return {"error": f"Only {len(rows)} resolved signals — need ≥ 20", "rows": len(rows)}

        import numpy as np
        probs = np.array([float(r["probability"]) for r in rows])
        wins  = np.array([1 if r["outcome"] in ("win","WIN",True,1) else 0 for r in rows])

        bin_edges    = np.linspace(0, 1, bins + 1)
        bucket_data  = []
        ece_sum      = 0.0

        for i in range(bins):
            lo, hi = bin_edges[i], bin_edges[i+1]
            mask = (probs >= lo) & (probs <= hi)
            n = mask.sum()
            if n == 0:
                continue
            avg_pred  = float(probs[mask].mean())
            actual_wr = float(wins[mask].mean())
            bucket_data.append({
                "bin_center":      round((lo+hi)/2, 2),
                "avg_predicted":   round(avg_pred, 3),
                "actual_win_rate": round(actual_wr, 3),
                "count":           int(n),
                "gap":             round(abs(avg_pred - actual_wr), 3),
            })
            ece_sum += (n / len(probs)) * abs(avg_pred - actual_wr)

        try:
            sb.table("calibration_checks").insert({
                "ece":           round(float(ece_sum), 4),
                "n_signals":     len(rows),
                "n_bins":        bins,
                "buckets":       bucket_data,
                "overconfident": ece_sum > 0.10,
            }).execute()
        except Exception as _se:
            log.warning(f"[reliability] Supabase save failed: {_se}")

        return {
            "ece":           round(float(ece_sum), 4),
            "n_signals":     len(rows),
            "buckets":       bucket_data,
            "overconfident": ece_sum > 0.10,
            "verdict":       "OVERCONFIDENT" if ece_sum > 0.10 else "WELL_CALIBRATED",
        }
    except Exception as e:
        return {"error": str(e)}

def plot_reliability(output_path: str = "reliability_diagram.png"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        data = compute_reliability()
        if "error" in data:
            log.warning(f"[reliability] cannot plot: {data['error']}")
            return None

        buckets = data["buckets"]
        x = [b["avg_predicted"] for b in buckets]
        y = [b["actual_win_rate"] for b in buckets]

        fig, ax = plt.subplots(figsize=(7, 7), facecolor="#0a0f1a")
        ax.set_facecolor("#111827")
        ax.plot([0,1],[0,1],"--",color="#4b5563",lw=1.5,label="Perfect calibration")
        ax.scatter(x, y, s=80, c=y, cmap="RdYlGn", vmin=0, vmax=1, zorder=5)
        ax.plot(x, y, "-", color="#14b8a6", lw=2, alpha=0.8)
        ax.set_xlim(0,1); ax.set_ylim(0,1)
        ax.set_xlabel("Predicted probability", color="#9ca3af")
        ax.set_ylabel("Actual win rate", color="#9ca3af")
        ax.set_title(
            f"Perseus Reliability Diagram\nECE={data['ece']:.3f} | {data['n_signals']} signals | {data['verdict']}",
            color="white", fontsize=11,
        )
        ax.tick_params(colors="#6b7280")
        for spine in ax.spines.values():
            spine.set_edgecolor("#374151")
        ax.legend(facecolor="#1f2937", labelcolor="#9ca3af")
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        return output_path
    except Exception as e:
        log.error(f"[reliability] plot failed: {e}")
        return None

if __name__ == "__main__":
    import json
    data = compute_reliability()
    print(json.dumps(data, indent=2))
