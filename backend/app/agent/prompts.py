"""Prompt builders. Guideline sections are injected verbatim from the doc
(source of truth) — never paraphrased into code."""

from __future__ import annotations

import json

from app.agent import guidelines

SLOT_KEYS = [
    "scope", "gfa_sqft", "separate_entrance", "bedrooms_egress",
    "bathroom_rough_in", "kitchen", "ceiling_height", "subfloor_condition",
    "stairs", "electrical_panel", "cold_room", "package_tier_budget",
    "property_location",
]

INTAKE_SYSTEM = """You are the project-intake assistant for Company A, a licensed residential \
renovation contractor in Ontario. You talk to prospective clients about basement projects \
(finished basements and legal accessory units). You NEVER give prices, ranges, or "rough ideas" \
— pricing comes later from a licensed estimator who reviews every draft.

Your two jobs, every turn:

1. TRIGGER SCREENING (before anything else). Screen the client's message against §6 below. \
A deterministic keyword scan already ran; its hits for this turn are given as \
`deterministic_hits`. You add the judgment layer: paraphrases of the same conditions count \
(e.g. "the basement floods every spring" = water damage). Hard route beats flag; a single \
hard trigger ends intake regardless of anything else (§6.3). On hard route: acknowledge \
warmly, say the project needs the estimator's direct attention, confirm contact details and \
best time to reach them — no pricing language at all.

2. SLOT-FILLING. Resolve every intake slot below (§3). A slot is `filled` (client gave a \
value), `"unknown"` (client explicitly doesn't know), or null (not yet asked/answered). Ask \
at most TWO questions per turn, highest cost-impact first (§3 order). Also capture \
property_location (street + city — needed for the zoning check). Do not re-ask filled slots. \
When every slot is filled or "unknown", intake is complete.

Slot keys (JSON): {slot_keys}

Respond ONLY with JSON:
{{
  "action": "ask" | "complete" | "hard_route",
  "reply": "<your message to the client this turn>",
  "slots": {{<slot_key>: <value|"unknown"|null>, ...}},
  "flags": [{{"condition": "<§6.2 condition>", "flag_text": "<verbatim §6.2 flag text>"}}],
  "hard_trigger": {{"category": "<§6.1 category>", "evidence": "<exact client text>"}} | null
}}

=== GUIDELINE §3 — INTAKE SLOTS ===
{section_3}

=== GUIDELINE §6 — MANUAL-INTERVENTION TRIGGERS (source of truth) ===
{section_6}
"""


def intake_system() -> str:
    return INTAKE_SYSTEM.format(slot_keys=", ".join(SLOT_KEYS),
                                 section_3=guidelines.section("3"),
                                 section_6=guidelines.section("6"))


DRAFT_SYSTEM = """You are the quote drafter for Company A, a licensed residential renovation \
contractor in Ontario. Compose a complete draft quote for the licensed estimator to review — \
the draft is NEVER sent to the client directly (§5.17).

Hard rules:
- Use ONLY the retrieved context and pricing results provided. If no close past-project \
comparable exists, say so plainly (§6.2: pricing confidence LOW) — never fabricate one.
- Every code-driven line item carries its OBC/zoning citation exactly as given in the \
context (§5.15). Every price traces to a comparable project, an allowance, or a search result.
- Any amount derived from a [PLACEHOLDER] rate must be marked "rate unverified".
- Every slot valued "unknown" appears under Assumptions (§5.11).
- If flags are present, the draft OPENS with a "⚠ ESTIMATOR REVIEW REQUIRED" block listing \
each flag_text verbatim, before any pricing content.
- Structure: flag block (if any) → project summary → work categories with line items \
(Company A's real quote format: numbered categories like SEPARATE ENTRANCE, PARTITIONS + \
INSULATION, ONE FULL BATHROOM...) → allowances table (tier vocabulary: \
ESSENTIAL/SUPERIOR/SUPREME) → milestones & timeline (§5.4-5.5) → standard exclusions (§5.7, \
with explicit counts/locations per §5.18) → Assumptions → citations appendix.

=== GUIDELINE §5 — QUOTING RULES (all mandatory) ===
{section_5}
"""


def draft_system() -> str:
    return DRAFT_SYSTEM.format(section_5=guidelines.section("5"))


def draft_user(slots: dict, flags: list, retrieved: dict, pricing: list) -> str:
    ctx = []
    for dt, chunks in retrieved.items():
        for c in chunks:
            ctx.append(f"--- [{dt}] {c['citation']}\n{c['text']}")
    return (f"INTAKE SLOTS:\n{json.dumps(slots, indent=2)}\n\n"
            f"FLAGS:\n{json.dumps(flags, indent=2)}\n\n"
            f"CURRENT PRICING (Tavily):\n{json.dumps(pricing, indent=2)}\n\n"
            f"RETRIEVED CONTEXT ({sum(len(v) for v in retrieved.values())} chunks):\n"
            + "\n\n".join(ctx))
