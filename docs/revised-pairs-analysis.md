# `_REVISED` quote-pair mining — what the estimator changed after the first draft

**Analyzed 2026-07-12** from the redacted corpus. Each original→revised diff is a labeled example of what a first-draft quote got wrong (or what the client renegotiated) — exactly the error distribution the estimation assistant exists to shrink, and the seed of the estimator-edit flywheel (`docs/product-analysis.md`).

Pairs with both versions on hand: P04→P05, P07→P08, P09→P10, plus the bonus same-property pair P21→P20 (two quotes for the same 745-sqft basement, Feb → May 2026). P03 and P12 exist only in revised form (no diffable original).

## Pair-by-pair findings

### P04 → P05 (Oakville, Millstone Dr): $135,000 → $100,000 (−26%)
Large budget-driven de-scope: dropped the second bathroom ("TWO BATHROOM(s)" → "BATHROOM"), dropped the kitchenette ("KITCHEN & KITCHENETTE" → "KITCHEN"), dropped the rec-room line, removed the promotional free feature wall, removed the city-permit-fee line. Milestone amounts re-spread ($20k steps → $10k steps).
**Signal**: revisions happen at the *scope-block* level (whole rooms), not the unit-price level. The client bought fewer blocks, not cheaper rates.

### P07 → P08 (Mississauga, Rochelle Way): $73,500 → $64,500 (−12%)
Trim + clarify: two sliding windows → one; kitchen cabinets 10 → 6 LNFT; "extra office shelves" milestone removed; deposit $15k → $10k; and the out-of-scope line "Kitchen Center Island" was amended to "Kitchen Center Island **(Upstairs & Downstairs)**".
**Signal**: (a) modest de-scoping; (b) an ambiguity — which floor's island is excluded — survived the first draft and had to be fixed in revision. Ambiguous count/location wording is a real revision driver.

### P09 → P10 (Mississauga, Nolan Road): $69,500 → $82,000 (+18%)
The one *upward* revision, and the most instructive: permit basis changed from **"Finished Basement" → "Accessory Basement Apartment"**. That single scope flip pulled in: electrical panel upgrade moved into scope (plus a new ADD-ONS menu with 200A upgrade, fire-rated door with self-close hinges, backsplash, extra windows), egress window re-specified ("Escape/Egress"), KITCHENETTE promoted to KITCHEN, laminate swapped to vinyl flooring, sqft corrected 700 → 750, milestones restructured around the full bathroom + full kitchen.
**Signal**: the Finished-Basement ↔ Accessory-Unit fork is the single largest cost decision (+$12.5k here), and the items it drags in are precisely the code-trigger cluster (egress, fire-rated door, panel capacity — guideline §7 / OBC 9.9.10, 9.10.9, 9.10.13).

### P21 (Feb) → P20 (May) — same property (Milton, Pringle Ave), both $94,950
Not a `_REVISED` pair by filename, but the same 745-sqft basement re-quoted three months later, pivoting from "Finished Basement **with rental provisions**" to "**Full Rental Legal Basement**" at an unchanged total. The trade: premium finishes went Included → Optional (media/feature wall, smart toilets, venetian plaster) while the legal-rental package came in (below-grade entrance with concrete cutting + upgraded exterior door, two enlarged egress windows 48"×42" and 30"×30" with wells, Toucan LVP + DMX subfloor + self-leveling; EV charger rough-in flipped to Included as a sweetener).
**Signal**: clients hold a budget number constant and re-negotiate *what fills it*; code-driven scope displaces finish scope, ~$15k of premium finishes ≈ one below-grade entrance + egress package.

## Aggregated signal → what it prioritizes

1. **Scope blocks, not unit prices.** All four revisions changed *what is included* (rooms, kitchen size, legal status, entrances); none haggled a rate. → The agent's leverage is intake accuracy and explicit scope-block pricing, not price precision. Confirms guideline §3's ordering: scope question first, budget band last-but-anchoring.
2. **The Finished↔Accessory fork appears in 2 of 4 pairs** (P09→P10 upward, P21→P20 sideways). → The agent should always be able to present the *delta package* between the two scopes for the same property (egress, entrance, fire separation, panel, kitchen class). Validates the Phase 1 OBC picks: 9.9.10, 9.10.9, 9.10.13, 9.41, and panel/alarm items.
3. **Windows/egress lines changed in 3 of 4 pairs** (count, spec, or addition). → High-variance intake question #4 (bedroom/egress) is confirmed as a top slot; window count/spec should never be assumed silently.
4. **Budget-target workflows are real** (P04→P05, P21→P20). → The draft should decompose into priced, removable scope blocks (the milestone schedule already approximates this decomposition — reuse it), so "get me to $100k" is a block-removal exercise, not a re-quote.
5. **Ambiguity is a revision driver** (P07→P08 center island). → New quoting rule added to the guideline doc (§5.18): items excluded or capped must state count and location explicitly.
6. **First drafts miss by double digits** (−26%, −12%, +18%, 0% at constant price). → This is the baseline for the product KPI "% of drafted line items the estimator edits" — the tool has to beat a human first draft that is routinely ±12-26% off.
7. **Deposits/milestones are re-derived from the total every time.** → Auto-derive them in the draft (deposit ≈ 15-26% band per guideline §5.3); never carry them over from a comparable.

## Demo-video material
The P09→P10 story arc ("client asked for a finished basement, then decided to make it a legal apartment — watch the code items and $12.5k flow in, each with its OBC citation") is a one-screen demonstration of the product's core join: code triggers, priced, inside the quote.
