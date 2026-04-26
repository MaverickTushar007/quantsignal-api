"""
domain/ml/research_protocol.py
Phase 6 — Quant Research Discipline.
Fill out ResearchSpec BEFORE coding. Run check_kill_criteria BEFORE deploying.
"""
from dataclasses import dataclass, field
from typing import List, Dict
import logging

log = logging.getLogger(__name__)


@dataclass
class ResearchSpec:
    hypothesis:              str
    data_required:           List[str]
    feature_rationale:       str
    expected_holding_period: str
    universe:                str
    known_risks:             List[str]
    kill_criteria:           str
    point_in_time_safe:      bool  = False
    cost_estimate_bps:       float = 20.0


@dataclass
class BacktestResult:
    sharpe_in_sample:        float
    sharpe_out_of_sample:    float
    max_drawdown:            float
    win_rate:                float
    avg_return_per_trade:    float
    net_of_costs:            float
    regime_breakdown:        Dict
    n_trades:                int
    passes_kill_criteria:    bool
    leakage_confirmed_clean: bool = False
    notes:                   str  = ""


KILL_CRITERIA = {
    "min_sharpe_oos":        0.30,
    "max_drawdown":          0.25,
    "min_win_rate":          0.50,
    "min_trades":            30,
    "must_be_net_positive":  True,
    "must_be_leakage_clean": True,
}


def check_kill_criteria(result: BacktestResult, spec: ResearchSpec) -> Dict:
    checks = {
        "sharpe_oos":      {"value": result.sharpe_out_of_sample,    "threshold": KILL_CRITERIA["min_sharpe_oos"],  "passed": result.sharpe_out_of_sample >= KILL_CRITERIA["min_sharpe_oos"]},
        "max_drawdown":    {"value": result.max_drawdown,             "threshold": KILL_CRITERIA["max_drawdown"],    "passed": result.max_drawdown <= KILL_CRITERIA["max_drawdown"]},
        "win_rate":        {"value": result.win_rate,                 "threshold": KILL_CRITERIA["min_win_rate"],    "passed": result.win_rate >= KILL_CRITERIA["min_win_rate"]},
        "n_trades":        {"value": result.n_trades,                 "threshold": KILL_CRITERIA["min_trades"],      "passed": result.n_trades >= KILL_CRITERIA["min_trades"]},
        "net_positive":    {"value": result.net_of_costs,             "threshold": 0.0,                              "passed": result.net_of_costs > 0},
        "leakage_clean":   {"value": result.leakage_confirmed_clean,  "threshold": True,                             "passed": result.leakage_confirmed_clean},
        "pit_safe":        {"value": spec.point_in_time_safe,         "threshold": True,                             "passed": spec.point_in_time_safe},
    }
    all_passed = all(c["passed"] for c in checks.values())
    failed     = [k for k, v in checks.items() if not v["passed"]]
    verdict    = "APPROVED" if all_passed else "REJECTED"
    if all_passed:
        log.info(f"[research_protocol] ✅ {verdict} — {spec.hypothesis[:50]}")
    else:
        log.warning(f"[research_protocol] ❌ {verdict} — failed: {failed}")
    return {"verdict": verdict, "all_passed": all_passed, "failed": failed, "checks": checks, "hypothesis": spec.hypothesis}


def leakage_audit_checklist(feature_name: str) -> List[str]:
    return [
        f"[ ] Is '{feature_name}' computed using ONLY data available AT signal time?",
        f"[ ] If news/sentiment — publish_time not crawl_time?",
        f"[ ] If earnings — announcement_date not filing_date?",
        f"[ ] If fundamentals — point-in-time values not restated?",
        f"[ ] No future price used in labeling that overlaps this feature?",
        f"[ ] Train/test split done by TIME (never random shuffle)?",
        f"[ ] Feature stationary (ADF test p < 0.05)?",
        f"[ ] Survives 6-month walk-forward OOS test?",
    ]


def print_spec(spec: ResearchSpec):
    """Pretty print a ResearchSpec for documentation."""
    print(f"\n{'='*60}")
    print(f"RESEARCH SPEC: {spec.hypothesis[:60]}")
    print(f"{'='*60}")
    print(f"Universe:        {spec.universe}")
    print(f"Holding period:  {spec.expected_holding_period}")
    print(f"Data needed:     {', '.join(spec.data_required)}")
    print(f"Rationale:       {spec.feature_rationale}")
    print(f"Known risks:     {', '.join(spec.known_risks)}")
    print(f"Kill criteria:   {spec.kill_criteria}")
    print(f"Cost estimate:   {spec.cost_estimate_bps}bps round-trip")
    print(f"PIT safe:        {'✅' if spec.point_in_time_safe else '❌ NOT CONFIRMED'}")
    print()
    print("LEAKAGE AUDIT:")
    for item in leakage_audit_checklist("all features"):
        print(f"  {item}")
