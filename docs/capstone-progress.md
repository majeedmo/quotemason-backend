# Capstone Progress Tracker

**Not part of the graded submission.** `docs/submission.md` and `docs/deliverables.md` are frozen — they describe the system as it was when submitted for evaluation, and stay untouched while grading is in progress. This doc tracks everything built *since* the submission, lives on `develop`, and is expected to keep changing.

## Branch state

- **`main`** — the graded submission. Last commit `d5f7de9`. **Do not merge into `main` until the user confirms grading is complete.**
- **`develop`** — active capstone branch. 18 PRs merged since the submission (`#6`–`#18`, following the submission's own PR numbering). All work happens on feature branches off `develop`, one PR per change, merged only on explicit go-ahead — same workflow as the original submission.
- Current test suite: **136 passed, 1 skipped**, no network required (`cd backend && uv run pytest`).

## `submission.md` §7.2's four capstone items — status

`submission.md` §7.2 ("What I would change or improve") named four specific follow-ups. Here's where each actually stands:

| # | Promised | Status | Notes |
|---|---|---|---|
| 1 | **Quote-accuracy evaluation** — leave-one-out on real projects, $ error + coverage | ✅ **Done** | `backend/app/evals/run_quote_accuracy_eval.py`, full writeup in `docs/quote-accuracy-eval.md`. 6 real projects (2 per tier), average absolute error **61.9% → 17.8%** after fixing 4 real takeoff/pricing bugs the eval surfaced, plus a data-grounded labor-rate calibration (PRs #13–#18) |
| 2 | **Material pricing** — replace live-Tavily-only search with a price tool/data source, refreshed by a scheduled agent | 🟡 **Partially done** | Sheet-first pricing exists and is live: `app/pricing/materials.py`, `allowances.py`, `labor.py` — Tavily is now only a fallback when a sheet lookup is missing/stale (PRs #11, #13). The "separate scheduled agent that refreshes daily" from the original plan is **not built** — the CSVs are still manually maintained/edited, not auto-refreshed |
| 3 | **Duplicate-quote guardrails** — reject/flag same email or address within a time window | ⬜ **Not started** | — |
| 4 | **Estimator authentication** — login + API role checks on `/quotes` | ⬜ **Not started** | — |

## Beyond §7.2 — architecture work not in the original plan

The user identified additional problems on re-reviewing the submitted architecture (zoning bylaw and building code folded into a generic RAG corpus, Tavily as the *only* pricing source, no traceability from a triggered code item to its cost impact). None of this was in `submission.md`'s Task 7 plan; all of it shipped before the quote-accuracy eval work above:

- **Contractor identity as config/metadata**, not hardcoded strings — `ContractorProfile`, `contractor_id` stamping/filtering, parametrized prompts (PR #6)
- **Regulatory lookup (OBC + zoning) as a shared, MCP-shaped tool service boundary**, separated from the generic/builder-specific corpus (PR #8)
- **3-stage traceable drafting pipeline** — `codes → takeoff → price_fill → draft` replacing the single drafting call, with a full `CodeItem ↔ TakeoffLine ↔ price_resolution` traceability chain so every dollar and every triggered code item can be followed back to its source (PR #9)
- **Point-estimate pricing policy** — job-size-band interpolation for labor rates, midpoint-of-range for materials/allowances (no size axis), tier-allowance wiring into `price_fill_node` (PRs #9, #13)
- **Corpus cleanup** — trimmed redundant legal/marketing boilerplate from past-quote text, extracted GFA into frontmatter/chunk metadata, re-ingested to Qdrant Cloud with zero retrieval regression (PR #12)

## What's left

Nothing below has started; no work begins on any of it until the user directs it:

- Duplicate-quote guardrails (§7.2 #3)
- Estimator authentication (§7.2 #4)
- Scheduled price-refresh agent (the unbuilt half of §7.2 #2)
- Further quote-accuracy calibration once Company A provides real labor-rate figures (the current 50% cut is data-grounded but explicitly a placeholder — see `docs/quote-accuracy-eval.md`)
