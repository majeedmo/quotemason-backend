# Eval Q4 — synthetic tier-pair ground truth

**Created 2026-07-12.** Eval Q4: *"Same spec as a completed past project, different finish level → cost delta should trace to the material swap."* The real corpus has no two quotes sharing a spec across package tiers, so two synthetic counterparts were generated in `corpus/quotes-synthetic/` (marked `synthetic: true`, `paired_with: <code>` in frontmatter).

**This file is the judge's answer key. It must NOT be ingested into the RAG corpus** — it lives in `docs/`, outside `corpus/`, precisely so the agent cannot retrieve the expected answer.

Construction rules used (both pairs):
- Same property, layout, GFA, and physical scope as the real counterpart; **only tier-controlled materials/finishes differ** (per `corpus/guidelines/material-allowances-DRAFT-v0.csv`).
- **Code-driven items are identical across tiers by design** (egress, fire separation, alarms, sprinkler, water separation, 200A panel) — tier never drops code items (guideline doc §2). A correct Q4 answer must NOT attribute any delta to code items.
- Delta magnitudes are drafter estimates (plausible, internally consistent), not owner-verified — fine for eval purposes since the eval scores *attribution*, not absolute dollars.

## Pair 1 — P19 (SUPERIOR, real) ↔ S01 (ESSENTIAL, synthetic)

Finished basement, 1,563 sqft, Oakville (Alfred Hughes Ave). P19 = $77,500; S01 = $62,000. **Delta = $15,500** (S01 cheaper).

| Category | SUPERIOR (P19) | ESSENTIAL (S01) | Δ |
|---|---|---|---|
| Wet bar/kitchenette | AGT flat panel + floating shelves, quartz $45/sqft (counter + island), KODAEN faucet, under-cabinet lighting | Shaker thermofoil MDF, quartz $30/sqft, Casa Maple faucet, no under-cabinet lighting | −$6,000 |
| Full bathroom | KODAEN rain-head system, curbless shower, lit niche, custom glass, floating vanity + LED mirror, skirted toilet, 24×48 tile $4.99 | Chrome shower set, sliding door + mosaic base, $500 wooden vanity + plain mirror, one-piece toilet, 24×24 tile $3.99 | −$4,800 |
| Powder room | Floating vanity + LED mirror, 24×48 tile | Wooden vanity + plain mirror, 24×24 tile | −$1,200 |
| Primer + paint | BM Regal + box ceiling with lime wash + ambient strip light | Standard BM, no ceiling feature | −$1,500 |
| Millwork | Double shaker doors, TAYMOR hardware | Single shaker doors, black handles | −$900 |
| Electrical | Low-voltage ambient lighting per design | None | −$1,100 |
| **Identical in both** | Egress window + well, framing/insulation/drywall, plumbing/HVAC rough-ins, cold room, PM, misc; flooring & feature wall out of scope in both | | $0 |

## Pair 2 — P20 (SUPREME, real) ↔ S02 (SUPERIOR, synthetic)

Legal basement apartment (accessory unit), 745 sqft, Milton (Pringle Ave). P20 = $94,950; S02 = $79,950. **Delta = $15,000** (S02 cheaper).

| Category | SUPREME (P20) | SUPERIOR (S02) | Δ |
|---|---|---|---|
| Kitchen | Painted MDF/AGT + built-in appliance panels, quartz counter AND backsplash $45/sqft, pot filler, peanut drawer | AGT flat panel, quartz counter $45 + tiled backsplash $10/sqft, standard KODAEN pulldown | −$4,500 |
| Full bathroom | Feature slab shower wall to $8.99/sqft, tiles $5.99, smart toilet, sconces | All walls 24×48 $4.99, skirted toilet, LED mirror only | −$3,600 |
| Flooring | Toucan 8–9 mm LVP $2.50 + tile zones with 4" tile baseboards | LVP $2.00 + tile in wet zones only, standard baseboards | −$1,200 |
| Stairs | Sand & stain + piano-look painted stringers | One-piece laminate caps/risers | −$900 |
| Millwork / feature wall | Media wall with AGT panels + lime wash | Painted feature wall | −$3,500 |
| Primer + paint | Lime wash living-room ceiling + ambient strip light | Standard BM Regal | −$1,300 |
| **Identical in both (must NOT be attributed)** | Drawings + permit, below-grade entrance, both egress window sets, fire-rated door, fire separations per permit, sprinkler, water separation manifold, 200A panel, interconnected strobe/smoke/CO alarms both floors, all rough-ins, glass bedroom wall, PM | | $0 |

## What a correct Q4 answer looks like

Asked e.g. "Project like P20 but SUPERIOR instead of SUPREME — what changes and what's the cost impact?", the agent should:
1. Retrieve both members of the pair (frontmatter `paired_with` or same street/city + tier difference).
2. State the total delta (≈$15k) and attribute it to the tier-controlled categories above (kitchen finishes, bathroom finishes, flooring allowance, stairs finish, feature wall, paint features).
3. Explicitly note that code-driven items do not change with tier.
4. Not invent scope differences that don't exist (layout, GFA, egress, permits).

Scoring: attribution categories correct (weight high) · code-items-unchanged stated (high) · delta magnitude within ±25% (medium) · no fabricated differences (high).
