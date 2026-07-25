# Capstone Progress Tracker

**Not part of the graded submission.** `docs/submission.md` and `docs/deliverables.md` are frozen — they describe the system as it was when submitted for evaluation, and stay untouched while grading is in progress. This doc tracks everything built *since* the submission, lives on `develop`, and is expected to keep changing.

## Branch state

- **`main`** — the graded submission. Last commit `d5f7de9`. **Do not merge into `main` until the user confirms grading is complete.**
- **`develop`** — active capstone branch. 19 PRs merged since the submission (`#6`–`#19`, following the submission's own PR numbering), plus PR #20 (duplicate-quote guardrails) open. All work happens on feature branches off `develop`, one PR per change, merged only on explicit go-ahead — same workflow as the original submission.
- Current test suite: **150 passed, 1 skipped**, no network required (`cd backend && uv run pytest`).

## `submission.md` §7.2's four capstone items — status

`submission.md` §7.2 ("What I would change or improve") named four specific follow-ups. Here's where each actually stands:

| # | Promised | Status | Notes |
|---|---|---|---|
| 1 | **Quote-accuracy evaluation** — leave-one-out on real projects, $ error + coverage | ✅ **Done** | `backend/app/evals/run_quote_accuracy_eval.py`, full writeup in `docs/quote-accuracy-eval.md`. 6 real projects (2 per tier), average absolute error **61.9% → 17.8%** after fixing 4 real takeoff/pricing bugs the eval surfaced, plus a data-grounded labor-rate calibration (PRs #13–#18) |
| 2 | **Material pricing** — replace live-Tavily-only search with a price tool/data source, refreshed by a scheduled agent | 🟡 **Partially done** | Sheet-first pricing exists and is live: `app/pricing/materials.py`, `allowances.py`, `labor.py` — Tavily is now only a fallback when a sheet lookup is missing/stale (PRs #11, #13). The "separate scheduled agent that refreshes daily" from the original plan is **not built** — the CSVs are still manually maintained/edited, not auto-refreshed |
| 3 | **Duplicate-quote guardrails** — reject/flag same email or address within a time window | 🟡 **PR open** | `backend/app/guardrails.py`: same normalized `scope + property_location` blocks a duplicate within a configurable expiry window (default 90 days); same email/phone blocks after too many distinct properties in that window (default 3). Alert is a best-effort LangSmith tag, no new notification infra (PR #20, not yet merged) |
| 4 | **Estimator authentication** — login + API role checks on `/quotes` | ⬜ **Not started** | — |

## Beyond §7.2 — architecture work not in the original plan

The user identified additional problems on re-reviewing the submitted architecture (zoning bylaw and building code folded into a generic RAG corpus, Tavily as the *only* pricing source, no traceability from a triggered code item to its cost impact). None of this was in `submission.md`'s Task 7 plan; all of it shipped before the quote-accuracy eval work above:

- **Contractor identity as config/metadata**, not hardcoded strings — `ContractorProfile`, `contractor_id` stamping/filtering, parametrized prompts (PR #6)
- **Regulatory lookup (OBC + zoning) as a shared, MCP-shaped tool service boundary**, separated from the generic/builder-specific corpus (PR #8)
- **3-stage traceable drafting pipeline** — `codes → takeoff → price_fill → draft` replacing the single drafting call, with a full `CodeItem ↔ TakeoffLine ↔ price_resolution` traceability chain so every dollar and every triggered code item can be followed back to its source (PR #9)
- **Point-estimate pricing policy** — job-size-band interpolation for labor rates, midpoint-of-range for materials/allowances (no size axis), tier-allowance wiring into `price_fill_node` (PRs #9, #13)
- **Corpus cleanup** — trimmed redundant legal/marketing boilerplate from past-quote text, extracted GFA into frontmatter/chunk metadata, re-ingested to Qdrant Cloud with zero retrieval regression (PR #12)

## Cost investigation: LLM spend per quote (2026-07-25)

Quote #22 cost **~$0.74 USD in LLM spend** (OpenRouter live pricing × actual LangSmith
token counts), ~87% of it the `takeoff`+`draft` Sonnet-5 calls. Three cost-reduction
ideas were tested via before/after runs against the real quote-accuracy eval cases
(the harness's own check against real historical project totals):

| Approach | Total LLM cost | Accuracy (single case, P11) | Accuracy (full 6-case suite, avg \|error\|) |
|---|---|---|---|
| Baseline (original prompts/models) | $0.75 (P11) / ~$0.70/quote (6-case avg) | -0.4% ✓ | **15.6%**, 3/6 within ±5% |
| Prompt-tightened ("be economical" instructions) | $0.83 (worse) | +11.1% ✗ | not tested at full-suite scale — reverted on the single-case result alone |
| Reasoning effort lowered (drafting model) | $0.39 (48% cheaper) | +23.1% ✗ (worse) | not tested at full-suite scale — reverted on the single-case result alone |
| **Cheaper model for `takeoff` only** (`claude-haiku-4.5` instead of `claude-sonnet-5`; `draft` unchanged) | **~$0.49/quote (6-case avg, ~30% cheaper)** | n/a | **11.7%**, 2/6 within ±5% |

The first two were reverted outright — same or worse cost *and* worse accuracy, no
trade-off to weigh. **The cheaper-takeoff-model result is different and genuinely
mixed, not a clean win or loss:** ~30% cheaper and a *lower* average absolute error
across all 6 real projects (11.7% vs 15.6%), but 2/6 cases land within the strict
±5% tolerance band instead of 3/6, and average takeoff-line coverage is a couple
points lower (93.2% vs 95.5%). The lower average error is pulled down by fixing one
case the baseline is bad at (P19: baseline +46.0% / +32.8% on two separate runs vs
cheap-takeoff +17.6%) at the cost of being worse on others (P16: -5.0% vs -18.0%).

Also surfaced during this investigation: **`app/agent/llm.py`'s OpenRouter clients have
no request timeout.** The baseline full-suite validation run hung for ~8.5 real hours
on a single `takeoff` call (case P21) that started, got zero response, and never
errored — confirmed via `ps` (process alive, ~16s total CPU time over 8h45m) and
LangSmith (`end_time: None` on that run). Cases before it in the run had already
completed successfully and were recovered from LangSmith traces (re-run through the
real `price_fill_node` to reconstruct their computed totals) rather than re-spending
on a second full 6-case run — only the missing 6th case was re-run standalone. This
is the same timeout gap noted in "Known characteristics" above, now confirmed as a
real (not just theoretical) failure mode.

**Status: implemented on this branch (`app/agent/nodes.py` takeoff_node now calls a
new `takeoff_model()` factory instead of `drafting_model()`), tests updated, but NOT
merged.** This is a real accuracy/cost trade-off on the system's core value
proposition (quote accuracy) — a judgment call for the user, not something to
auto-ship on a favorable-looking average. Needs a decision: keep on Sonnet for
takeoff, adopt the cheaper model, or investigate further (e.g. more repeated runs per
case — LLM sampling noise is large enough here, per the single-case P11 numbers
swinging from -0.4% to +23% run-to-run at temperature 0.3, that a single 6-case pass
per condition is suggestive, not conclusive).

## What's left

Nothing below has started; no work begins on any of it until the user directs it:

- Estimator authentication (§7.2 #4)
- Scheduled price-refresh agent (the unbuilt half of §7.2 #2)
- Further quote-accuracy calibration once Company A provides real labor-rate figures (the current 50% cut is data-grounded but explicitly a placeholder — see `docs/quote-accuracy-eval.md`)
- Decide on the cheaper-takeoff-model branch above (merge, keep as Sonnet, or run more repetitions first)
- Add a request timeout (+ retry/fallback) to `app/agent/llm.py`'s OpenRouter clients — confirmed live 2026-07-25, not just theoretical: a hung call currently waits forever instead of failing over
