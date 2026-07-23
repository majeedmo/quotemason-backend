---
jurisdiction: 'ontario'
doc_type: 'builder_guideline'
title: 'Company A — Estimating & Quoting Guidelines'
source_version: 'DRAFT v0 (2026-07-12) — NOT owner-reviewed'
effective_date: '2026-07-12'
source_note: 'Drafted from ~20 real Company A quotes (2024-2026) + OBC 2024 Part 9 Phase 1 extracts. Pending review by business owner — see Review Status.'
---

# Company A — Estimating & Quoting Guidelines (DRAFT v0)

## Review status — read first

This document is a **stand-in draft** assembled for the estimation-assistant build. Every fact is tagged:

- **[GROUNDED]** — taken directly from real executed Company A quotes (2024–2026) or the Ontario Building Code 2024 extracts in `corpus/OBC/part9_phase1/`.
- **[PLACEHOLDER]** — plausible industry-typical value inserted by the drafter. **The business owner must verify every placeholder before this document is treated as authoritative.** The agent may use placeholders for draft quotes but must mark any line item derived from one as "rate unverified."

Companion spreadsheets (same folder):
- `labor-rates-DRAFT-v0.csv` — labor rates by trade × job-size band (small <500 sqft, medium 500–1,000 sqft, large 1,000–2,000 sqft). **All rates [PLACEHOLDER].**
- `material-allowances-DRAFT-v0.csv` — material allowances per package tier. **All values [GROUNDED]** in real quotes.

---

## 1. Service scopes

Company A quotes basement work under two distinct scopes. **Never mix their vocabularies** — they carry different code obligations and price levels. [GROUNDED]

| | **Finished Basement** | **Accessory Unit / Basement Apartment** |
|---|---|---|
| What it is | Rec room / living space for the same household; may include wet bar, bathroom | Legal self-contained second dwelling unit, typically rental |
| Kitchen | Wet bar / kitchenette only | One full kitchen (required) |
| Permit path | Building permit for interior alterations | Permit as legal second unit ("change of use") — see §7 |
| Entrance | Existing stairs | Separate/below-grade entrance typical |
| Typical extras vs. finished basement | — | Egress window(s), fire separations, interconnected strobe/smoke/CO alarms both units, water separation manifold per dwelling, furnace-room fire sprinkler, 200 A panel upgrade, kitchen exhaust/dryer rough-ins |
| Timeline | 8–10 weeks | 10–12 weeks |
| Deposit to book | $15,000–$25,000 + HST (scales with project value) | same |

Anything outside these two scopes (new build, addition, kitchen/bath makeover upstairs, commercial) is quoted under a different template — **the estimation assistant must escalate those to the estimator, not draft.** [GROUNDED — Company A runs separate contract formats for those.]

## 2. Package tiers — what each includes

Three named tiers: **ESSENTIAL / SUPERIOR / SUPREME**. Tier controls finish level and allowances, **not** code compliance — code items (§7) are driven by scope, never dropped for a lower tier. [GROUNDED]

Work categories used in every quote (keep these headings — they are the quote schema): **Architectural/Permit · Separate Entrance & Windows · Kitchen · Bathroom(s) · Flooring & Stairs · Primer + Paint · Partitions + Insulation · Millwork · Electrical · Plumbing · HVAC/Gas · Cold Storage · Project Management · Misc.** [GROUNDED]

Tier deltas (detail in `material-allowances-DRAFT-v0.csv`): [GROUNDED]

- **ESSENTIAL** — Shaker thermofoil MDF kitchen, quartz to $30/sqft, 24×24 tile to $3.99/sqft, $500 vanity, 7–8 mm LVP to $2.00/sqft, one-piece laminate stair caps, single-shaker doors, standard sliding shower door.
- **SUPERIOR** — AGT flat-panel cabinets + floating shelves, quartz to $45/sqft, 24×48 tile to $4.99/sqft, curbless shower + rain head + linear drain + lit niche, KODAEN floating vanity + LED mirror, skirted toilet, box/lime-wash ceiling feature with low-voltage ambient light, double-shaker doors, TAYMOR hardware.
- **SUPREME** — everything in SUPERIOR plus: quartz backsplash, pot filler, built-in appliance panels, feature slab shower wall to $8.99/sqft, smart toilets, 8–9 mm LVP to $2.50/sqft, tile zones with tile baseboards, sand-and-stain stairs ("piano look"), media/feature wall with AGT + lime wash, glass partition walls where designed. SUPREME quotes commonly bundle architectural drawings + city permit fees **into** scope (ESSENTIAL/SUPERIOR normally exclude them).
- Constant across tiers: pot lights **max 40** across the basement; Benjamin Moore paint; Rockwool "Safe 'n Sound" insulation; DMX 1-Step subfloor; drywall with machine mud, fibre/mesh tape, corner beads.

## 3. High-variance intake questions (slot-filling — the agent must resolve ALL before drafting)

These are the questions that most swing basement price. Each slot is `filled | explicitly-unknown`; an estimate drafted with unknowns must list them as assumptions. Order = descending typical cost impact. [Impact directions GROUNDED in quote structure; magnitudes PLACEHOLDER]

1. **Scope: finished basement or legal accessory unit?** Splits the entire quote template (§1); accessory adds $15k–$40k of code-driven items.
2. **Total basement GFA (sqft)?** Primary driver of framing, drywall, flooring, paint. Real projects run 700–2,000 sqft.
3. **Separate/below-grade entrance needed?** Concrete cutting + excavation + door: one of the largest single line items ($10k–$25k range).
4. **Bedrooms — how many, and does each have a code-size egress window or door?** Each missing egress window = concrete cutting + window + well + drainage (§6, OBC 9.9.10).
5. **Bathroom: existing rough-in below slab, or new?** Existing rough-in nearby vs. breaking slab swings plumbing 30–50%.
6. **Kitchen or wet bar? Gas or electric stove?** Full kitchen adds cabinetry allowance, counters, exhaust rough-in, possibly gas line.
7. **Ceiling height and bulkheads/ducts?** Under ~7 ft finished (see OBC 9.5.3 extract for exact minimums) may need duct relocation or is a feasibility flag; gas line to flex-pipe conversion avoids ceiling drops.
8. **Subfloor condition: slab level and dry?** Self-leveling beyond 3–4 bags, or any water infiltration history, changes scope (leaky basement repair is excluded by default — §5).
9. **Stairs: keep, refinish, or relocate?** Relocation is structural + permit scope.
10. **Electrical panel capacity — 100 A or 200 A?** Accessory units get the 200 A upgrade by default in real quotes.
11. **Cold room / storage to be finished?** Adds spray-foam + waterproofing scope.
12. **Package tier preference (ESSENTIAL/SUPERIOR/SUPREME) — and budget band?** Sets all allowances.

## 4. Material calculation rules of thumb

All **[PLACEHOLDER — owner to verify]** unless noted. Waste factors included.

- **Framing**: linear ft of partition wall ≈ GFA × 0.55; studs @ 16" o.c. + plates ≈ 1 stud per linear ft × 1.15 waste.
- **Drywall**: sheet count ≈ (wall linear ft × height + ceiling sqft) ÷ 32 × 1.10. Use 5/8" Type X where a fire separation is required (§7), ½" elsewhere. [Type-X-where-required GROUNDED]
- **Insulation**: Rockwool Safe 'n Sound in ceiling + furnace room + bathroom walls (SUPERIOR/SUPREME also living/kitchen zones) [GROUNDED]; sqft ≈ ceiling area + perimeter walls of noise rooms.
- **Flooring**: LVP sqft = floor area − tiled zones, × 1.08 waste; tile × 1.12 waste (large format).
- **Paint**: 1 gal per ~350 sqft per coat; spray primer everywhere, two finish coats [GROUNDED as method].
- **Self-leveling concrete**: include 2 bags (ESSENTIAL) to 3–4 bags (SUPREME) by default; more is a site-condition change order. [GROUNDED]
- **Pot lights**: ~1 per 40–50 sqft of living area, cap 40. [cap GROUNDED]
- **Doors**: count = bedrooms + baths + storage + furnace room + closets; add one 20-min fire-rated self-closing door at the suite boundary for accessory units. [GROUNDED]

## 5. Quoting rules — custom instructions (the agent MUST adhere to all of these)

Drawn from Company A's real contract boilerplate. [GROUNDED except where marked]

1. **Allowance-based pricing.** Materials are quoted as "up to $X per sqft/unit" allowances (see CSV). Never quote a specific SKU price as guaranteed; overages on client selections are client-paid.
2. **HST**: all prices exclude HST; HST added at invoicing. State this on every quote.
3. **Deposit**: required to book a start date — $15k (ESSENTIAL-scale) to $25k (SUPREME-scale), roughly 20–26% of contract value.
4. **Milestone payments**: structure the schedule against completed stages, ending with a small handover balance (real pattern: signing → entrance/windows → framing+HVAC rough-in → electrical+plumbing rough-in → drywall/primer → bathroom → flooring/doors → kitchen + handover). Final milestone $2.5k–$7.5k.
5. **Timeline**: quote 8–10 weeks (finished basement) / 10–12 weeks (accessory unit) from construction start, not contract signing.
6. **Change orders**: all changes signed off by PM + client; client must not direct sub-trades; admin fee up to $500 per change order after finishes are finalized or work has begun; every change gets its own priced document.
7. **Standard exclusions** (list on every quote): permits & city fees and minor-variance approvals (unless SUPREME bundles them), municipal parking fees, landscape/driveway/porch work, glass railings/pickets, appliances and their installation, feature walls/fireplaces (add-on), ceiling soundproofing via resilient channel + Sonopan walls (add-on), DMX-plus-plywood subfloor upgrade (add-on), heated floors, EV charger rough-in, professional closet organizers, water-infiltration/leaky-basement repair, duct trunk relocation.
8. **Warranty**: one-year service warranty on all installations; work to current building codes, drawings, specifications; workmanlike manner.
9. **Work hours**: 7:30 AM–8:30 PM band per contract version; outside hours needs client approval.
10. **Scope freeze**: contract price is based on the approved scope; after sign-off the scope is frozen and changes go through §5.6.
11. **Assumptions must be printed.** The estimate is based on client information + site visit + assumption that prior work met standard practice; unforeseen conditions become work orders. Any slot from §3 left "unknown" appears in the quote under Assumptions.
12. **Payment methods**: cash, direct deposit, certified cheque, e-transfer. Include HST # and WSIB # on the quote. Include licensing block (Tarion, municipal renovator licence, WSIB, $5M CGL insurance).
13. **Site sign board** clause included by default.
14. **Portable toilet** ($2,000) if client won't provide bathroom access. [GROUNDED]
15. **Every code-driven line item must carry its OBC citation** (§7) so the client-facing quote can explain *why* the item exists. [Build-specific instruction, not from Company A boilerplate]
16. **Out-of-scope detection**: see §6 — the manual-intervention trigger list is the authoritative rule set. [Build-specific]
17. **No auto-send — ever.** Drafts go to the estimator for review/approval. [Hard product principle]
18. **Excluded or capped items must state count and location explicitly** (e.g. "Kitchen Center Island (Upstairs & Downstairs)" not "Kitchen Center Island"; "up to 6 LNFT of cabinets" not "cabinets"). [GROUNDED — mined from the `_REVISED` pairs: an ambiguous exclusion survived a first draft and forced a revision]
19. **Every priced line must show its source, or be flagged unpriced.** Ground each dollar amount, in this order of preference: (a) a comparable past project (name the code); (b) a tier allowance or [PLACEHOLDER] labour/material rate from the CSV (name it — and mark any [PLACEHOLDER]-derived amount "rate unverified"); (c) a current price check (the material price sheet with its updated date, or a named web search). Bundled trade work — electrical, plumbing, HVAC, project management — is the usual offender: price it from its [PLACEHOLDER] labour rate (cite the CSV, mark "rate unverified"), not as a bare number. Contract-policy amounts — the deposit (§5.3), milestone balances (§5.4–5.5), the portable-toilet allowance (§5.14), and change-order admin fees (§5.6) — cite the rule number. Only when nothing above grounds a line (no comparable, allowance, rate, or price check) quote it as "estimator to price — no comparable on file". A dollar figure with no visible source is not permitted, including in the allowances table, milestone schedule, and totals. [Build-specific instruction — Company A prices from prior jobs and sub-trade quotes, never guesses]

## 6. Manual-intervention triggers — when the agent must route to the human estimator

The agent screens every client message against this section **before and during** intake. Two tiers:

- **HARD ROUTE** — stop drafting immediately. Collect no further slots beyond basic contact info, tell the client an estimator will follow up personally, and hand the estimator everything gathered so far plus the trigger that fired. The agent never produces a number, a range, or a "rough idea" for these.
- **FLAG** — continue intake and draft normally, but the draft opens with a prominent flag block the estimator must acknowledge before the quote can be sent.

Detection is two-layer: (a) a deterministic keyword scan using the lists below (case-insensitive, match on stems — "underpin" catches "underpinning"), and (b) the agent's own judgment for paraphrases of the same conditions. The keyword lists in this section are the source of truth; extend them here, not in code. [All build-specific; keyword lists PLACEHOLDER pending owner review — §9]

### 6.1 HARD ROUTE triggers

| Category | Why routed | Keywords / signals |
|---|---|---|
| Out-of-scope project type | Different contract template & pricing; challenge scope is basement work only | "new build", "custom home", "addition", "garden suite", "laneway suite", "ADU", "tear down", "second storey", "garage conversion", "kitchen makeover" / "bathroom makeover" (upstairs), "commercial", "office", "retail", "restaurant", "warehouse", "plaza" |
| Structural work | Engineering + site assessment required before any number is defensible | "underpinning", "bench footing", "lowering the floor/basement", "load-bearing", "remove wall", "beam", "post", "foundation crack/repair", "sagging", "settling" |
| Hazardous / damaged site | Remediation scope unknowable without inspection; liability | "asbestos", "vermiculite", "mold"/"mould", "flood", "water damage", "leaky basement", "sewage backup", "fire damage", "smoke damage" |
| Insurance / restoration | Insurance-claim quoting is a different format entirely (RCV/ACV) — never quote it from renovation templates | "insurance claim", "adjuster", "restoration", "Belfor" |
| Permit avoidance | Refuse politely; company only works to code and permit (§5) | "without a permit", "no permit", "skip the permit", "cash job", "under the table", "don't tell the city" |
| Legalization of existing unpermitted work | Requires site inspection + municipal negotiation | "already finished", "legalize", "make it legal", "unpermitted", "illegal suite", "retroactive permit", "as-built" |
| Zoning / approvals beyond by-right | Requires planning review, not estimating | "minor variance", "committee of adjustment", "heritage", "conservation authority", "easement", "more than one unit"/"two apartments" (>1 secondary suite) |
| Legacy electrical / servicing | Scope explodes unpredictably | "knob and tube", "aluminum wiring", "fuse panel", "60 amp", "well water", "septic" |
| Tenancy complications | Legal exposure | "tenant", "eviction", "tenanted", "renters living there" |
| Contract-term changes | Only the owner varies warranty/payment/deposit terms | client asks to change warranty, deposit, payment schedule, or liability terms |
| Size out of band | Outside the corpus's competence envelope (700–2,000 sqft real projects) | stated GFA < 400 or > 2,500 sqft |
| Unrealistic budget/timeline | Signals mismatch better handled by a human conversation | stated budget < $40k for an accessory unit; requested completion < 6 weeks from today |

### 6.2 FLAG triggers (draft continues, estimator must acknowledge)

| Condition | Flag text the draft must carry |
|---|---|
| Ceiling height reported < 7'6" or unknown, or heavy bulkheads/ducts | "Feasibility: verify clear height on site vs. OBC 9.5.3 minimums before commitment" |
| No close past-project comparable found (spec/sqft/tier) | "Pricing confidence LOW — no comparable project in corpus; totals extrapolated" (never fabricate a comparable) |
| ≥3 intake slots explicitly unknown | "Draft based on assumptions listed — site visit required before sending" |
| Any line item priced from a [PLACEHOLDER] labor rate exceeding 15% of total | "Rate unverified — owner review of <trade> rate required" |
| Below-grade / walkout entrance in scope | "Excavation priced at band midpoint — soil/depth/access unverified" |
| Stair relocation requested | "Structural + permit implications — estimator to confirm scope" |
| Client-supplied numbers inconsistent (e.g., sqft vs. room count implausible) | "Intake inconsistency: <detail> — verify before sending" |
| Electrical panel capacity unknown for accessory unit | "200 A upgrade assumed — verify panel" |

### 6.3 Routing behavior

1. **To the client (hard route)**: acknowledge, state that this project needs the estimator's direct attention, confirm contact details and best time; no pricing language at all.
2. **To the estimator (both tiers)**: a routing packet — trigger(s) fired with the exact matched text, all slots gathered so far, the conversation transcript reference, and (flag tier) the draft with its flag block.
3. **Logging**: every routing event is tagged in LangSmith (`route=hard|flag`, `trigger=<category>`) — routing precision/recall is an eval metric (seed: eval Q6), and false-trigger review is how this list gets tuned.
4. **Precedence**: hard route beats flag; any single hard trigger ends drafting even if every slot is filled and a strong comparable exists.

## 7. Code-trigger checklist (accessory units especially)

Map intake answers to these triggers; cite the section on the quote line. Extracts live in `corpus/OBC/part9_phase1/`.

| Trigger | OBC 2024 citation | Typical line items [GROUNDED as scope lines] |
|---|---|---|
| Any basement bedroom | 9.9.10 (egress from bedrooms) | Egress window cut/enlarge + steel lintel + window well + drainage to weeper, guard if by stairs |
| Second dwelling unit created | 9.41 (change of use); Division A 1.4.1.2 "secondary suite" | Permit as legal second unit; compensating construction |
| Suite boundary | 9.10.9 (fire separations); 9.10.13 (closures) | 5/8" Type X drywall assemblies; 20-min fire-rated self-closing door |
| Both units share furnace/services | 9.10.9 / permit condition | Furnace-room fire sprinkler; smoke detector on furnace; water separation manifold per dwelling |
| Alarms | 9.10.19 (smoke), 9.32.3.9/9.32.3.9A (CO) | Strobe/smoke/CO alarms across basement AND upstairs upgrade (interconnection) |
| Habitable-room sizes/heights | 9.5.3, 9.5.3A–F | Feasibility check before layout; duct/gas-line relocation to protect height |
| Sound between units | 9.11.1 | Rockwool in ceiling; resilient channel + Sonopan offered as add-on |
| New/enlarged openings | 9.7.1–9.7.2 | Window specs, argon-filled vinyl per package |
| Stairs/handrails/guards | 9.8.1–9.8.4, 9.8.7–9.8.8 | Stair refinish must maintain rise/run compliance; guardrail on window by stairs |
| Bathroom/kitchen ventilation | 9.32 | 80 CFM exhaust fans; kitchen/dryer exhaust rough-ins |

## 8. Price anchors from real projects [GROUNDED]

Use as sanity bands for drafted totals, not as quotes:

| Project shape | Tier | GFA | Total (pre-HST) | $/sqft |
|---|---|---|---|---|
| Accessory unit conversion (kitchen, bath, entrance, panel upgrade) | ESSENTIAL | ~1,000 (est.) | $77,500 | ~$78 |
| Finished basement (kitchenette, bath + powder, flooring TBD) | SUPERIOR | 1,563 | $77,500 | $50 |
| Legal rental suite (drawings + permit in scope) | SUPREME | 745 | $94,950 | $127 |

Rule of thumb pending owner review: **finished basement ≈ $45–65/sqft; accessory unit ≈ $75–130/sqft** (smaller GFA → higher $/sqft because kitchen/bath/entrance costs don't scale down). [PLACEHOLDER band fitted to grounded points]

## 9. Open questions for the business owner (next-phase review)

1. Verify/replace every labor rate in `labor-rates-DRAFT-v0.csv` (all placeholders).
2. Confirm the job-size bands (small/medium/large) match how you actually price.
3. Confirm §7 $/sqft bands and where the tier premiums really sit (ESSENTIAL→SUPERIOR→SUPREME uplift %).
4. Confirm the §3 question order matches what you ask first on a site visit — and what's missing.
5. Rules of thumb in §4: correct factors?
6. Which exclusions in §5.7 do you sometimes bring in scope, and at what price?
7. Sub-trade vs. in-house: which trades are subbed, and do subbed trades carry a markup rule?
8. Contingency: do you carry an explicit % for unforeseen conditions or rely purely on change orders?
9. §6 manual-intervention triggers: review both tiers — which hard-route items would you actually want a draft for anyway, which flags should be hard routes, and what keywords/situations are missing from your experience (the "call me immediately" cases)? Also confirm the out-of-band thresholds (GFA 400–2,500 sqft, accessory-unit budget floor $40k, 6-week rush timeline).
