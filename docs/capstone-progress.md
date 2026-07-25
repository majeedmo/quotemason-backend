# Capstone Progress Tracker

**Not part of the graded submission.** `docs/submission.md` and `docs/deliverables.md` are frozen — they describe the system as it was when submitted for evaluation, and stay untouched while grading is in progress. This doc tracks everything built *since* the submission, lives on `develop`, and is expected to keep changing.

## Branch state

- **`main`** — the graded submission. Last commit `d5f7de9`. **Do not merge into `main` until the user confirms grading is complete.**
- **`develop`** — active capstone branch. 19 PRs merged since the submission (`#6`–`#19`, following the submission's own PR numbering), plus PR #20 (duplicate-quote guardrails) open. All work happens on feature branches off `develop`, one PR per change, merged only on explicit go-ahead — same workflow as the original submission.
- Current test suite: **155 passed, 1 skipped**, no network required (`cd backend && uv run pytest`).

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

## Known characteristics (investigated, not a bug)

- **Complex jobs take minutes to draft, synchronously.** A legal-basement-apartment
  full-conversion request (quote #22, thread `web-20c418c7-ce23`, 2026-07-25) took
  ~8.5 min end-to-end in the `_finish_draft` background task before appearing in
  the estimator queue. Traced in LangSmith: `codes` 43s, `takeoff` 289s, `price_fill`
  5s (no LLM), `draft` 176s. The two slow stages are each a single non-streamed
  `ChatOpenAI` completion — 30,856 and 19,720 completion tokens respectively — not
  a hang, retry loop, or bad OpenRouter routing. Checked against 431 historical LLM
  calls: duration scales linearly with completion tokens (~100-110 tok/s) all the
  way up this list, so it's reproducible for any job that triggers this much
  code/takeoff detail, not a one-off. `app/agent/llm.py`'s `ChatOpenAI` clients have
  no explicit `timeout`, so a call that actually hung (vs. merely being large) would
  wait indefinitely rather than failing over to the OpenRouter fallback model.
  No fix requested yet — noting here so the next time this comes up we don't
  re-diagnose from scratch.

## Bugs found and fixed since the last update

- **Silent material-price loss on category-name mismatch (quote #22, 2026-07-25).**
  Auditing quote #22's 5 unpriced line items turned up 3 different causes, only
  one of which was an actual bug: (a) two were genuine price-sheet gaps
  (no exterior/entrance-door item on the sheet; `stairs/finish` is spec-only at
  every tier with no material fallback category) — real gaps, not fixed;
  (b) two were smoke-alarm code-compliance lines the takeoff model correctly
  bundled into `electrical_rough_and_finish` (`$0` by design) but
  `price_fill_node` mislabels them "estimator to price" same as a real gap —
  cosmetic, not fixed; (c) **interior paint material (~$2,700) was silently
  dropped** because the takeoff model wrote takeoff-line category `"painting"`
  while the sheet's real category is `"paint"` — an exact-match miss on
  `materials.lookup()` even though `(paint, interior_paint)` was on the sheet
  the whole time. Fixed: `materials.lookup()` now falls back to an
  item-name-only match when the exact `(category, item)` pair misses and the
  item name is unambiguous across the sheet (mirrors the existing tolerance
  for the takeoff model echoing `"category/item"` into the item field).
  Regression tests in `test_material_prices.py`.

## What's left

Nothing below has started; no work begins on any of it until the user directs it:

- Estimator authentication (§7.2 #4)
- Scheduled price-refresh agent (the unbuilt half of §7.2 #2)
- Further quote-accuracy calibration once Company A provides real labor-rate figures (the current 50% cut is data-grounded but explicitly a placeholder — see `docs/quote-accuracy-eval.md`)
