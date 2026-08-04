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

- **Three rendering faults on quote line items (quote #120, 2026-08-04, PR #64).**
  Found while rehearsing the Demo Day walkthrough. All three were in
  `app/agent/draft_render.py` — the pipeline computed the right data every time and
  the renderer dropped it, so no pricing logic changed and the contract total was
  unaffected. (a) **Line items looked duplicated**: a takeoff line normally resolves
  into two `price_resolution` rows — material off the sheet, labour off the rate
  table — and both inherit the *takeoff line's* description, so the same sentence
  printed twice at two different prices (8 of quote #120's 30 lines). Rows now carry
  a `— material` / `— labour` component label. (b) **Code-driven lines never showed
  their clause**: the `takeoff.code_item_ref → codes_checklist.citation` link exists,
  but `price_resolution` rows don't copy it, so by render time the clause was
  unreachable and the Source column fell back to the price basis. Zero OBC citations
  appeared in any table row across quotes #113, #118 and #120 — despite §5.19
  requiring the clause *on the line*, and despite the demo pitch resting on exactly
  that. `_code_citations()` rebuilds the join. (c) **The document contradicted
  itself**: a redundant `unpriced` row sat alongside a real price for the same line,
  putting an already-costed item into section 18's "this total excludes the following
  unpriced lines" disclaimer; `_drop_redundant_unpriced()` suppresses it. Root cause
  of (c) is in `price_fill`, not the renderer — see the open defect below. Verified
  by replaying quote #120's stored state through the real renderer: 40 price rows →
  39, total unchanged at $74,894.32. Suite 237 → 240 passed.

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

**Status: MERGED (PR #24, `5bfe8d2`).** `app/agent/nodes.py`'s `takeoff_node` now
calls a dedicated `takeoff_model()` factory (own `TAKEOFF_MODEL` setting, currently
`claude-haiku-4.5`) instead of reusing `drafting_model()`. This was a real
accuracy/cost trade-off on the system's core value proposition (quote accuracy), not
an auto-ship-on-a-favorable-average call — decided by the user after reviewing the
mixed eval result above. See the real-world validation below for confirmation against
live post-merge quotes.

### Real-world validation: quotes #22–#24 (2026-07-25)

Three actual production quotes drafted the same day, pulled from LangSmith traces
(OpenRouter live pricing × actual token counts, chain-level start/end timestamps for
per-stage compute time). #22 and #23 both predate the takeoff-model merge (`takeoff`
still on Sonnet-5); #24 is the first real quote drafted after it merged:

| Stage | #22 (thread `web-20c418c7-ce23`) | #23 (thread `web-62c815b2-ff8e`) | #24 (thread `web-201aac58-44d8`) |
|---|---|---|---|
| intake | 4.32s | 17.85s | 10.97s |
| codes | 42.61s | 30.69s | 24.86s |
| takeoff | 288.59s (Sonnet-5) | 169.23s (Sonnet-5) | 34.99s (**Haiku-4.5**) |
| price_fill (no LLM) | 5.09s | 6.12s | 4.95s |
| draft | 176.27s | 241.83s | 297.39s |
| **Compute time** | **516.88s (8m 37s)** | **465.72s (7m 46s)** | **373.15s (6m 13s)** |
| **Wall clock** | 518.92s (8m 39s) | 595.74s (9m 56s) | 416.67s (6m 57s) |
| **Cost** | **$0.7340** | **$0.5970** | **$0.4473** |

#24 is both the cheapest and fastest of the three: 39% cheaper than #22, 25% cheaper
than #23, and 2–3.5 minutes faster on compute time — almost entirely the
Haiku-takeoff switch (289s/169s → 35s). `draft` time doesn't track completion tokens
across these three (50,576 → 43,279 → 30,257 tokens, but draft time went 176s →
242s → 297s) — likely OpenRouter/Sonnet latency variance rather than a real
regression, worth another look only if it keeps trending up. This matches the
eval-harness numbers above almost exactly: baseline ~$0.70–0.75/quote vs.
cheaper-takeoff ~$0.49/quote average — #22/#23 are baseline-era, #24 is the first
live confirmation of the merged config's real-world cost.

## Open defect: `price_fill` emits a guaranteed-unpriced row for spec-only allowances (2026-08-04)

**Deferred until after Demo Day.** PR #64 masked the symptom in the renderer; the
root cause is still live in `price_fill_node` (`app/agent/nodes.py`, the
`if allowance_item:` branch around L807-825).

**What happens.** A takeoff line can carry *both* `item` and `allowance_item`, and
each goes down its own branch, so one line emits up to three price rows. Quote #120's
interior paint (`t28`) is the reference case:

| takeoff field | branch taken | result |
|---|---|---|
| `item: paint/interior_paint` | `_price_material` | ✅ $2,502.00 — supplier list |
| `allowance_item: paint/brand` | allowance → **material-sheet fallback** | ❌ `unpriced` — "no fresh sheet price (missing)" |
| `trade: painting` | `labor.lookup` | ✅ $2,750.00 |

`allowances.lookup("paint", "brand")` finds a row, but `quoted_value` is `None`
because brand/quality is a **spec-only cell at ESSENTIAL** — it describes what you
get, it isn't a separate charge. The code then falls back to the material sheet
"under the same key", i.e. it looks up an item literally called `brand`. No price
sheet will ever have that, so this fallback **cannot succeed** for descriptor-style
allowance keys; it only ever produces an unpriced row.

**Why it mattered.** That phantom row rendered as a third table line repeating the
same description, and — worse — pushed an already-costed item into section 18's
*"this total excludes the following unpriced lines and is not yet final"* disclaimer,
so the quote contradicted itself on screen. Spotted during demo rehearsal.

**Two candidate fixes, neither attempted:**

1. *Narrow* — skip the allowance→material fallback when the same takeoff line already
   priced a material through `item`. Cheap, but leaves the dead lookup in place.
2. *Principled* — don't fall back at all when the allowance row **exists** and is
   spec-only at this tier. A spec-only cell means "included, no separate charge",
   which is a different thing from "no allowance row found". Only the genuinely
   missing-row case should reach the material sheet.

(2) looks right, but it needs checking against the double-charge guard immediately
above it (L795-805: pricing `item` *and* `allowance_item` together silently
double-charged 6-15% of each quote across 22 real quotes / 43 lines) — the two
branches interact, so change them together and re-run the quote-accuracy eval rather
than reasoning about it in isolation.

**Scope check before fixing:** only `t28` in quote #120 had both a priced and an
unpriced row, so this is not yet known to be widespread — worth counting
`price_source == "unpriced"` rows whose takeoff line also priced, across several
stored quotes, to size the problem before choosing a fix.

## What's left

Nothing below has started; no work begins on any of it until the user directs it:

- The `price_fill` spec-only-allowance defect documented immediately above
- Estimator authentication (§7.2 #4)
- Scheduled price-refresh agent (the unbuilt half of §7.2 #2)
- Further quote-accuracy calibration once Company A provides real labor-rate figures (the current 50% cut is data-grounded but explicitly a placeholder — see `docs/quote-accuracy-eval.md`)
- Add a request timeout (+ retry/fallback) to `app/agent/llm.py`'s OpenRouter clients — confirmed live 2026-07-25, not just theoretical: a hung call currently waits forever instead of failing over
