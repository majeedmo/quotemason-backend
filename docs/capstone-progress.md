# Capstone Progress Tracker

**Not part of the graded submission.** `docs/submission.md` and `docs/deliverables.md` are frozen — they describe the system as it was when submitted for evaluation, and stay untouched while grading is in progress. This doc tracks everything built *since* the submission, lives on `develop`, and is expected to keep changing.

## Branch state

- **`main`** — the graded submission. Last commit `d5f7de9`. **Do not merge into `main` until the user confirms grading is complete.**
- **`develop`** — active capstone branch. 21 PRs merged since the submission (`#6`–`#21`, following the submission's own PR numbering: duplicate-quote guardrails and the demo estimator login are the latest), plus PR #23 (exterior-door price + category-mismatch pricing fix) open. All work happens on feature branches off `develop`, one PR per change, merged only on explicit go-ahead — same workflow as the original submission.
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

## Cost investigation, round 2: GLM-5.2 for the draft stage (2026-07-25, NOT adopted)

Follow-up to the takeoff-model experiment above: with `takeoff` already on
`claude-haiku-4.5`, tested swapping `draft` from `claude-sonnet-5` to `z-ai/glm-5.2`
(intake/codes/takeoff unchanged, still Haiku). Validated the same way — full 6-case
quote-accuracy suite, real historical totals:

| Configuration | Cost/quote (6-case avg) | Avg absolute error | Within ±5% tolerance | Avg coverage |
|---|---|---|---|---|
| Baseline (Sonnet takeoff + Sonnet draft) | ~$0.70 | 15.6% | 3/6 | 95.5% |
| Haiku takeoff + Sonnet draft (branch above) | ~$0.49 | **11.7% (best)** | 2/6 | 93.2% |
| Haiku everywhere + **GLM-5.2 draft** | **$0.18 (cheapest)** | **19.7% (worst)** | **1/6 (worst)** | 96.8% |

**Not adopted.** GLM-5.2 is far cheaper (~4x cheaper than baseline) but produced the
worst accuracy of all three configurations tested — worse than doing nothing. It also
reproduced the exact hang failure mode documented above for Sonnet: during the full
6-case validation run, the GLM-5.2 draft call for case P16 started, returned zero
response, and never errored for 9+ minutes (P11's and P12's GLM draft calls *did*
complete, taking 5.5-6 min each — not actually faster than Sonnet despite being
marketed as a lighter model). Recovered the same way as the earlier Sonnet hang:
killed the process, reconstructed the 3 already-completed cases from LangSmith traces
via the real `price_fill_node` (accuracy only depends on `takeoff` output, not
`draft` text, so a stuck draft call doesn't block reconstructing that case's number),
and re-ran only the 3 remaining cases standalone — no case was double-paid-for.

Conclusion: this isn't a real trade-off to weigh the way the takeoff-model change
was — it's strictly worse on accuracy than the Haiku-takeoff branch while also being
unreliable, and the extra cost savings beyond that branch aren't worth trading away
most of the accuracy gain that branch already secured. Reverted; not merged, not
carried forward as an open question.

## Cost investigation, round 3: GLM-5.2 on takeoff AND draft (2026-07-25, abandoned)

One more variant, on a limited 3-case sample (one per tier — P11/essential, P16/superior,
P20/supreme) rather than the full 6, since the direction was already looking bad:
GLM-5.2 for *both* `takeoff` and `draft` (intake/codes still Haiku). Run case-by-case
(not the full-suite batch) specifically to contain the risk of another multi-hour hang.

- **P11: +44.2% error, 93% coverage** — worse than every other configuration tested
  so far (baseline 15.6%, Haiku-takeoff 11.7%, GLM-draft-only 19.7% average; this
  single case alone beats all of those averages in the wrong direction). Takeoff
  itself also got noticeably slower and more verbose on GLM (33,752 completion
  tokens, ~9 min for one call — more verbose than Sonnet's takeoff ever was).
- **P16: hung on the `takeoff` call itself** (not just draft this time) — zero
  response for 62+ minutes, no error, killed manually. Retried once; abandoned before
  the retry finished once P11's number came back as clearly the worst result of the
  whole investigation — no point spending more to confirm a direction that's already
  this one-sided.
- P20 not run.

**Abandoned, not adopted.** Putting GLM-5.2 on *both* stages compounds both failure
modes seen separately above (worse accuracy, and the unreliable-hang behavior) rather
than averaging them out. This closes out the GLM-5.2 line of investigation — Haiku
takeoff + Sonnet draft (two sections up) remains the only configuration that beat the
original baseline on accuracy.

## What's left

Nothing below has started; no work begins on any of it until the user directs it:

- Estimator authentication (§7.2 #4)
- Scheduled price-refresh agent (the unbuilt half of §7.2 #2)
- Further quote-accuracy calibration once Company A provides real labor-rate figures (the current 50% cut is data-grounded but explicitly a placeholder — see `docs/quote-accuracy-eval.md`)
- The recurring OpenRouter hang (now seen on both Sonnet and GLM-5.2) makes a request timeout on `app/agent/llm.py`'s clients higher priority, not lower — two separate models have shown this failure mode now
- Decide on the cheaper-takeoff-model branch above (merge, keep as Sonnet, or run more repetitions first)
- Add a request timeout (+ retry/fallback) to `app/agent/llm.py`'s OpenRouter clients — confirmed live 2026-07-25, not just theoretical: a hung call currently waits forever instead of failing over
