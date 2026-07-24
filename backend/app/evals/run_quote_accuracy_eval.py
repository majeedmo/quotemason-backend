"""Quote-accuracy eval: runs the pipeline against reconstructed specs of real
past projects and compares its computed price to each project's actual
historical total, targeting within +/-5%.

Bypasses intake by invoking the drafting stages directly (codes -> takeoff ->
price_fill -> draft), not via graph.invoke() -- the only existing intake-skip
lever, estimator_feedback, is not an inert routing flag: its string value
gets injected verbatim into the codes/takeoff/draft prompts as a live
"ESTIMATOR REVISION REQUEST", which would corrupt every eval run. Each stage
node is the exact function production uses; this just orchestrates them
without LangGraph.

price_fill_node is deterministic (no LLM) -- its price_resolution rows are
what "the accuracy eval can assert" per that node's own docstring, so the
computed total sums extended_quoted_cad across PRICED rows only. An
"unpriced" row is an explicit "estimator to price" flag, not a $0 claim --
summing it as zero would silently bias the computed total low, so unpriced
lines are excluded from the sum and reported separately as a coverage
metric (share of takeoff lines actually priced).

Live-API script (real models + live Qdrant required, same as
run_scenario_eval.py):

    cd backend && uv run python -m app.evals.run_quote_accuracy_eval \
        [--only qa-p19-superior-finished-basement] [--json out.json]
"""

from __future__ import annotations

import argparse
import json

from app.agent.nodes import codes_node, draft_node, price_fill_node, takeoff_node
from app.evals.dataset import QuoteAccuracyCase, load_quote_accuracy_cases


def run_pipeline(initial_state: dict) -> dict:
    """codes -> takeoff -> price_fill -> draft, merging each node's return
    dict into the running state -- mirrors what the LangGraph edges do,
    without the estimator_feedback side effect graph.invoke() would carry."""
    state = dict(initial_state)
    for node in (codes_node, takeoff_node, price_fill_node, draft_node):
        state.update(node(state))
    return state


def pct_error(computed: float, actual: float) -> float:
    if actual == 0:
        return 0.0 if computed == 0 else float("inf")
    return (computed - actual) / actual * 100.0


def within_tolerance(pct_err: float, tolerance_pct: float) -> bool:
    return abs(pct_err) <= tolerance_pct


def _line_breakdown(rows: list[dict]) -> list[dict]:
    """The full price_resolution table, sorted by extended $ descending --
    the diagnostic view: if a case misses tolerance, the largest-dollar
    lines and their exact source/rate are immediately visible."""
    keys = ("takeoff_line_ref", "category", "item", "trade", "allowance_item",
           "quantity", "unit", "price_source", "unit_price_quoted_cad",
           "extended_quoted_cad")
    return sorted(
        ({k: r.get(k) for k in keys} for r in rows),
        key=lambda r: r.get("extended_quoted_cad") or 0, reverse=True)


def run_case(case: QuoteAccuracyCase) -> dict:
    initial_state = {"slots": case.slots,
                     "_eval_exclude_project_codes": case.exclude_project_codes}
    final = run_pipeline(initial_state)
    rows = final.get("price_resolution") or []
    priced = [r for r in rows if r.get("price_source") != "unpriced"]
    unpriced = [r for r in rows if r.get("price_source") == "unpriced"]
    computed_total = round(sum(r.get("extended_quoted_cad") or 0 for r in priced), 2)
    err = pct_error(computed_total, case.actual_total_cad)
    coverage_pct = round(len(priced) / len(rows) * 100.0, 1) if rows else 0.0
    return {
        "project_code": case.project_code,
        "computed_total_cad": computed_total,
        "actual_total_cad": case.actual_total_cad,
        "pct_error": round(err, 2),
        "within_tolerance": within_tolerance(err, case.tolerance_pct),
        "tolerance_pct": case.tolerance_pct,
        "coverage_pct": coverage_pct,
        "unpriced_lines": [r.get("description") or r.get("item") or r.get("trade")
                          or r.get("allowance_item") or "" for r in unpriced],
        "line_breakdown": _line_breakdown(priced),
        "codes_checklist": final.get("codes_checklist"),
        "takeoff": final.get("takeoff"),
        "draft": final.get("draft"),
        "notes": case.notes,
    }


def run(only: str | None = None) -> dict:
    cases = load_quote_accuracy_cases()
    if only:
        cases = [c for c in cases if c.id == only]
    return {"cases": {c.id: run_case(c) for c in cases}}


def render_markdown(report: dict) -> str:
    lines = ["### Quote-accuracy eval (computed vs. actual historical total)", "",
             "| case | project | computed | actual | % error | within tolerance | coverage |",
             "|---|---|---|---|---|---|---|"]
    for cid, r in report["cases"].items():
        within = "✓" if r["within_tolerance"] else "✗"
        lines.append(
            f"| {cid} | {r['project_code']} | ${r['computed_total_cad']:,.0f} | "
            f"${r['actual_total_cad']:,.0f} | {r['pct_error']:+.1f}% | "
            f"{within} (±{r['tolerance_pct']:.0f}%) | {r['coverage_pct']:.0f}% |")
    n = len(report["cases"])
    n_within = sum(1 for r in report["cases"].values() if r["within_tolerance"])
    lines += ["", f"**{n_within}/{n} cases within tolerance.**"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run a single case id")
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args()

    report = run(only=args.only)
    print(render_markdown(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n(full report -> {args.json})")


if __name__ == "__main__":
    main()
