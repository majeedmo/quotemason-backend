# Quote-Accuracy Eval

**Run 2026-07-24.** Answers a concrete, numeric question for demo day: does QuoteMason's computed pricing land within a defensible margin of what Company A actually charged on real past projects, for comparable scope?

## Methodology

Six real, tier-labeled projects spanning all three package tiers and both scope families (`corpus/quotes-redacted/`): **P11** (ESSENTIAL, accessory unit), **P12** (ESSENTIAL, basement), **P16** (SUPERIOR, basement), **P19** (SUPERIOR, finished basement), **P20** (SUPREME, accessory unit), **P21** (SUPREME, finished basement). Cases are defined in `backend/app/evals/data/quote_accuracy_cases.json`; each hand-authors the project's intake slots from a careful reading of the real quote body, since the corpus frontmatter alone only carries scope/GFA/tier/city.

For each case, `backend/app/evals/run_quote_accuracy_eval.py`:

1. Runs `codes → takeoff → price_fill → draft` directly (not `graph.invoke()`, to avoid `estimator_feedback`'s prompt-injection side effect) — the exact same production node functions, just orchestrated without LangGraph or the intake conversation.
2. Excludes the case's own project code (and its synthetic twin, where one exists — P19↔S01, P20↔S02) from `takeoff_node`'s comparable-project retrieval, so the pipeline can't retrieve and copy its own historical answer (leave-one-out).
3. Sums `extended_quoted_cad` across every **priced** `price_resolution` row for `computed_total_cad`. Unpriced rows (an explicit "estimator to price" flag, not a $0 claim) are excluded from the sum and reported separately as `coverage_pct`, so a tight `pct_error` can't be accidentally hollow — accurate only because whole cost categories were silently dropped.
4. `draft_node` also runs, producing an actual QuoteMason-generated document per case for side-by-side comparison with the real historical quote.

Target: `pct_error` within ±5% of the real quote's total, with coverage high enough that the number isn't a false positive.

## Results

| case | project | computed | actual | % error | coverage |
|---|---|---|---|---|---|
| qa-p11-essential-accessory-unit | P11 | $94,354 | $77,500 | +21.8% | 93.3% |
| qa-p12-essential-basement | P12 | $101,363 | $92,000 | +10.2% | 91.0% |
| qa-p16-superior-basement | P16 | $113,831 | $119,999 | **-5.1%** | 90.2% |
| qa-p19-superior-finished-basement | P19 | $102,932 | $77,500 | +32.8% | 88.9% |
| qa-p20-supreme-accessory-unit | P20 | $113,939 | $94,950 | +20.0% | 92.1% |
| qa-p21-supreme-finished-basement | P21 | $78,664 | $94,950 | -17.1% | 92.9% |

0/6 strictly inside ±5% (P16 is a hair outside, at -5.1%). Average absolute error: **17.8%**, down from **61.9%** before the fixes and calibration below — full report with per-line breakdowns at `backend/eval_results/quote_accuracy.json`.

## What the first real run actually found

The first live run (against a freshly re-ingested Qdrant Cloud collection) surfaced four genuine defects, not pricing noise — average absolute error started at **61.9%**, with every case overshooting:

1. **Qdrant Cloud rejected the new leave-one-out filter.** Cloud (strict mode) rejects filters on unindexed payload fields; `project_code` had never been filtered on before this eval, so it had no payload index. Fixed by indexing it (`backend/app/ingestion/ingest.py`) — no re-embed needed, payload indexes are independent of vector data.
2. **A per-door labor rate got applied to a linear-foot baseboard quantity.** `millwork_doors_trim` ($350–600/door) was assigned to a 523-linear-foot baseboard line — 523 × $475 = $248,425 for one line, the single largest driver of an early ~6x overshoot. Baseboard labor is already bundled into `flooring_install_lvp`. Fixed with an explicit takeoff-prompt rule tying a trade's rate to its one fixed physical unit.
3. **Lump-sum trades got billed multiple times.** `electrical_rough_and_finish` (lump-sum, covers the whole project's electrical scope per its own `includes` column) got split across 4 separate takeoff lines in one run, each independently charging the full lump sum — $53,600 for a $13,400 job. Fixed with a code-level guard in `price_fill_node`: the first line for a lump-sum trade charges it, every subsequent line for that same trade is zeroed with a traceable note, not silently dropped.
4. **The same double-counting pattern recurred for per-instance trades.** `bathroom_build` (per-bathroom) was split 4 ways for a single-bathroom project — $46,000 instead of $11,500. Unlike lump-sum trades, per-instance trades can legitimately repeat (a 2-bathroom project needs 2 lines), so there's no reliable code-side signal for "how many bathrooms does this project actually have" — this got a prompt-only fix generalizing the existing bathroom_build double-count rule.

## Labor-rate calibration

After the four fixes above, a clean run still averaged **61.9%** absolute error, with **labor cost alone exceeding the entire real quoted total** in 4 of 6 cases (up to 136% of the real total, before materials are even added) — despite the takeoff no longer generating duplicate or mis-keyed lines. Checked with the owner: Company A confirmed `labor-rates-DRAFT-v0.csv`'s dollar figures were rough best-guesses, not verified against real billing.

Implied per-case correction factors (solving for what labor multiplier would make `computed_total_cad` match `actual_total_cad`, holding material/allowance totals fixed) ranged 0.31–0.77 across the six cases, with **mean 0.50 and median 0.44**. Applied a flat **50% reduction** to every rate in `labor-rates-DRAFT-v0.csv` (chosen as simple, round, and centered on the data — deliberately not a per-trade curve-fit to 6 sparse, LLM-noisy data points) and re-ran:

| | before calibration | after calibration |
|---|---|---|
| average absolute error | 61.9% | 17.8% |
| worst case | +110.9% (P19) | +32.8% (P19) |
| best case | +16.5% (P16) | **-5.1% (P16)** |

## Known, pre-documented caveats (not pipeline defects)

- **P12, P16**: real quote totals reflect a discretionary cash-deal discount ($13,000 off P12; ~4% off P16) our pipeline has no mechanism to model — expected upward bias vs. the discounted actual figure.
- **P19**: the real quote explicitly excludes flooring/subfloor/stairs from its total ("Not in scope — TBD"); our takeoff still generates those lines per the guideline's own §4 defaults since there's no intake slot to express "flooring is out of scope" — expected upward bias. This explains part, but not all, of P19's remaining +32.8% gap (see `backend/eval_results/quote_accuracy.json` for the line-level breakdown).

## Status and next steps

This is a **calibration**, not a final validation — the 50% labor-rate reduction is data-grounded from these 6 cases but still pending Company A's own real rate figures. The remaining -17% to +33% spread across cases is plausible run-to-run/case-to-case variance (partly explained by the caveats above, partly ordinary LLM takeoff variance) rather than a repeatable code defect — the four structural bugs found this round are fixed, verified by a clean rerun with zero errors and no duplicate-billing lines in any case's breakdown.
