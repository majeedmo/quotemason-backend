"""Deterministic renderer for the final quote document.

Stage 3 used to be one giant LLM call producing the whole markdown document
from scratch every time -- section presence, order, headings, and per-
category formatting were the model's discretion every run, which is why
identical intake specs produced structurally different quotes (20-34
takeoff lines, different section sets, sometimes an entire missing
category, confirmed live 2026-07-26). This module assembles every section
directly from already-computed data (price_resolution, takeoff, slots,
citations); draft_node's only remaining LLM call produces a
`schemas.DraftNarrative` (project summary + pricing confidence) that gets
spliced into sections 0-1 below -- everything else needs arithmetic or a
fixed template, not judgment.

See corpus/guidelines/builder-guidelines-DRAFT-v0.md §2 for the canonical
15-heading work-category schema this mirrors (§ references throughout this
module cite that document, per the existing citation discipline), and
app/pricing/quote_sections.py for the category -> section mapping.
"""

from __future__ import annotations

from app.agent import schemas
from app.config import settings
from app.pricing import allowances, quote_sections

# The takeoff verifier's omission checks name an intake *slot*, not a
# category (there's no line to read a category off) -- this lets an
# injected "verifier_flagged" placeholder land in the section it's
# actually about instead of a generic Misc bucket.
_OMISSION_SLOT_SECTION: dict[str, tuple[int, str]] = {
    "bathroom_rough_in": (7, "Bathroom(s)"),
    "kitchen": (8, "Wet Bar/Kitchenette/Kitchen"),
    "separate_entrance": (4, "Separate Entrance & Windows"),
    "bedrooms_egress": (4, "Separate Entrance & Windows"),
    "electrical_panel": (11, "Electrical"),
    "cold_room": (14, "Cold Storage"),
    "stairs": (6, "Flooring & Stairs"),
    "subfloor_condition": (6, "Flooring & Stairs"),
}


def _row_section(row: dict) -> tuple[int, str]:
    category = row.get("category", "")
    if category == "verifier_flagged":
        ref = str(row.get("takeoff_line_ref", ""))
        slot = ref[len("omission-"):] if ref.startswith("omission-") else ""
        return _OMISSION_SLOT_SECTION.get(slot, quote_sections.MISC_SECTION)
    return quote_sections.section_for(category)


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _pretty_instance(instance: str) -> str:
    return instance.replace("_", " ").strip().title()


def _row_source(row: dict) -> str:
    """One short citation per §5.19 -- every priced line must show its
    source, or be flagged unpriced with a reason."""
    source = row.get("price_source")
    if source == "price_sheet":
        text = row.get("source_detail") or "price sheet"
    elif source == "allowance":
        text = row.get("source_detail") or "tier allowance"
    elif source == "labor_rate":
        text = row.get("source_detail") or "labor rate"
    elif source == "tavily":
        text = f"web price check ({row.get('query', '')})"
    elif source == "estimator_override":
        text = "estimator-provided price"
    else:
        return f"estimator to price — {row.get('note') or 'no comparable on file'}"
    notes = []
    if row.get("rate_unverified"):
        notes.append("rate unverified")
    if row.get("site_dependent"):
        notes.append("confirm on-site before finalizing")
    if row.get("note"):
        notes.append(row["note"])
    return text + (f" ({'; '.join(notes)})" if notes else "")


def _row_line(row: dict) -> str:
    desc = (row.get("description") or row.get("item") or row.get("allowance_item")
           or row.get("trade") or row.get("category") or "")
    qty = row.get("quantity")
    qty_str = f"{qty:g}" if isinstance(qty, (int, float)) else "—"
    unit = row.get("unit", "")
    return (f"| {desc} | {qty_str} {unit} | {_fmt_money(row.get('extended_quoted_cad'))} "
           f"| {_row_source(row)} |")


_TABLE_HEADER = "| Item | Qty | Amount (CAD) | Source |\n|---|---|---|---|"


def _na_reason(section_number: int, slots: dict) -> str:
    """Deterministic when a section maps directly to an intake slot; a
    generic, still-honest reason otherwise -- never an LLM call."""
    if section_number == 4:
        entrance = str(slots.get("separate_entrance", "")).lower()
        if "no new construction" in entrance or "exists" in entrance:
            return ("not applicable: separate entrance and egress already exist, "
                    "no new construction, per intake")
    if section_number == 14:
        cold_raw = slots.get("cold_room")
        cold = str(cold_raw or "").strip().lower()
        if cold in ("", "none", "no", "n/a"):
            shown = cold_raw if isinstance(cold_raw, str) and cold_raw.strip() else "not specified"
            return f"not applicable: no cold room in scope (intake: cold_room = {shown!r})"
        return "estimator to price — no cold-storage material/labor rate on file for this scope"
    if section_number == 2:
        return ("excluded from contract price per §5.7 (permits & city fees), unless "
                "bundled into a SUPREME-tier quote per §2")
    if section_number == 15:
        return "estimator to price — no project-management fee schedule on file"
    return "no items identified in the takeoff for this project"


def _render_cost_section(number: int, heading: str, rows: list[dict],
                         slots: dict) -> tuple[str, float]:
    if not rows:
        return f"## {number}. {heading}\n\n$0.00 — {_na_reason(number, slots)}\n", 0.0
    instances: dict[str, list[dict]] = {}
    for r in rows:
        instances.setdefault(r.get("instance") or "", []).append(r)
    if "" in instances and len(instances) == 2:
        # An unlabeled row (e.g. the takeoff verifier's injected omission
        # placeholder, which has no instance of its own since it isn't tied
        # to any real takeoff line) joins the section's one real instance
        # rather than splitting into its own "no instance" heading for what
        # is physically the same bathroom/kitchen -- confirmed live
        # 2026-07-26: a single-bathroom project's omission placeholder for
        # "bathroom_rough_in" rendered as a bare "## 7. Bathroom(s)" block
        # alongside "## 7. Bathroom(s) — Bathroom 1" for the priced lines.
        # Ambiguous with 2+ real instances, so this only merges when there's
        # exactly one to attribute it to.
        [other] = [k for k in instances if k != ""]
        instances[other] = instances[other] + instances.pop("")
    blocks = []
    total = 0.0
    for instance in sorted(instances):
        inst_rows = instances[instance]
        block_heading = (f"## {number}. {heading} — {_pretty_instance(instance)}"
                         if instance else f"## {number}. {heading}")
        table = "\n".join([_TABLE_HEADER] + [_row_line(r) for r in inst_rows])
        subtotal = round(sum(r.get("extended_quoted_cad") or 0 for r in inst_rows), 2)
        total += subtotal
        blocks.append(f"{block_heading}\n\n{table}\n\nCategory subtotal: {_fmt_money(subtotal)}\n")
    return "\n".join(blocks), round(total, 2)


def _resolve_tier(slots: dict) -> str | None:
    tb = str(slots.get("package_tier_budget", "") or "").upper()
    for t in ("ESSENTIAL", "SUPERIOR", "SUPREME"):
        if t in tb:
            return t
    return None


def _render_cover(state: dict, narrative: "schemas.DraftNarrative") -> str:
    slots = state.get("slots") or {}
    flags = state.get("flags") or []
    lines = ["# DRAFT QUOTE SUMMARY", ""]
    lines.append(f"**Property:** {slots.get('property_location', 'unknown')}")
    scope = slots.get("scope", "unknown scope")
    gfa = slots.get("gfa_sqft")
    tier = _resolve_tier(slots) or "unknown"
    lines.append(f"**Project:** {scope}"
                 + (f", {gfa:g} sqft" if isinstance(gfa, (int, float)) else "")
                 + f" — {tier} tier")
    lines.append(f"**Pricing confidence:** {narrative.pricing_confidence}")
    for reason in narrative.confidence_reasons:
        lines.append(f"- {reason}")
    if flags:
        lines.append("")
        lines.append("**⚠ ESTIMATOR REVIEW REQUIRED**")
        for f in flags:
            lines.append(f"- {f.get('flag_text', f.get('condition', ''))}")
    return "\n".join(lines) + "\n"


def _render_project_summary(narrative: "schemas.DraftNarrative") -> str:
    return f"## 1. Project Summary\n\n{narrative.project_summary}\n"


def _render_allowances_table(tier: str | None) -> str:
    rows = allowances.load_allowances()
    lines = ["## 19. Allowances Table (per tier)", ""]
    if tier:
        lines.append(f"This quote is priced at the **{tier}** tier.")
    lines.append("")
    lines.append("| Category | Item | ESSENTIAL | SUPERIOR | SUPREME |")
    lines.append("|---|---|---|---|---|")
    for (cat, item), row in sorted(rows.items()):
        lines.append(f"| {cat} | {item} | {row.essential} | {row.superior} | {row.supreme} |")
    return "\n".join(lines) + "\n"


def _render_milestones(slots: dict) -> str:
    scope = str(slots.get("scope", "")).lower()
    weeks = "10–12 weeks" if "accessory" in scope else "8–10 weeks"
    return (
        "## 20. Milestones & Timeline\n\n"
        f"Timeline: {weeks} from construction start, not contract signing (§5.5).\n\n"
        "Milestone schedule, against completed stages (§5.4):\n\n"
        "1. Signing / deposit — $15,000–$25,000 (roughly 20–26% of contract value), "
        "due to book a start date (§5.3)\n"
        "2. Separate entrance & windows complete\n"
        "3. Framing + HVAC rough-in complete\n"
        "4. Electrical + plumbing rough-in complete\n"
        "5. Drywall + primer complete\n"
        "6. Bathroom complete\n"
        "7. Flooring + doors complete\n"
        "8. Kitchen complete + handover — final balance $2,500–$7,500 (§5.4)\n\n"
        "Exact dollar amounts for milestones 2-7 are set by the estimator against "
        "the approved trade schedule and the total contract value above; only the "
        "deposit and final-milestone ranges are fixed policy (§5.3-§5.4).\n"
    )


_STANDARD_EXCLUSIONS = [
    "Permits & city fees and minor-variance approvals (unless bundled into a SUPREME-tier quote)",
    "Municipal parking fees",
    "Landscape / driveway / porch work",
    "Glass railings / pickets",
    "Appliances and their installation",
    "Feature walls / fireplaces (available as an add-on)",
    "Ceiling soundproofing via resilient channel + Sonopan walls (available as an add-on)",
    "DMX-plus-plywood subfloor upgrade (available as an add-on)",
    "Heated floors",
    "EV charger rough-in",
    "Professional closet organizers",
    "Water-infiltration / leaky-basement repair",
    "Duct trunk relocation",
]


def _render_exclusions() -> str:
    body = "\n".join(f"- {x}" for x in _STANDARD_EXCLUSIONS)
    return (
        "## 21. Standard Exclusions\n\n"
        f"{body}\n\n"
        "Per §5.18, any project-specific excluded or capped item called out within "
        "the work-category sections above states its count and location explicitly "
        "(e.g. \"kitchen center island\" with a stated linear-footage cap), never a "
        "bare category name.\n"
    )


def _render_terms() -> str:
    hst_pct = f"{settings.hst_rate * 100:g}%"
    return (
        "## 22. Standard Terms & Conditions\n\n"
        f"- All prices exclude HST; HST ({hst_pct}) is added at invoicing (§5.2).\n"
        "- Materials are quoted as \"up to $X per sqft/unit\" allowances (§5.1); "
        "overages on client selections are client-paid.\n"
        "- Changes after sign-off go through a signed change order (PM + client); "
        "an admin fee of up to $500 per change order applies once finishes are "
        "finalized or work has begun; every change gets its own priced document (§5.6).\n"
        "- One-year service warranty on all installations; work to current building "
        "codes, drawings, and specifications, in a workmanlike manner (§5.8).\n"
        "- Standard work hours: 7:30 AM-8:30 PM; work outside this band needs client "
        "approval (§5.9).\n"
        "- Contract price is based on the approved scope; after sign-off the scope is "
        "frozen and changes go through the change-order process above (§5.10).\n"
        "- Payment methods: cash, direct deposit, certified cheque, e-transfer. "
        "HST # and WSIB # included on the quote, with licensing block (Tarion, "
        "municipal renovator licence, WSIB, $5M CGL insurance) (§5.12).\n"
        "- Site sign board included by default (§5.13).\n"
        "- A portable toilet ($2,000) is added if the client cannot provide bathroom "
        "access during construction (§5.14).\n"
    )


def _render_assumptions(slots: dict, takeoff_assumptions: list[str]) -> str:
    lines = ["## 23. Assumptions", ""]
    unknown_slots = [k for k, v in slots.items()
                     if v is None or str(v).strip() == "" or str(v).strip().lower() == "unknown"]
    for k in unknown_slots:
        lines.append(f"- {k}: unknown — estimate assumes a typical/standard condition; "
                     "confirm on site (§5.11).")
    for a in takeoff_assumptions or []:
        lines.append(f"- {a}")
    if len(lines) == 2:
        lines.append("- None — every intake slot was filled and the takeoff stated no "
                     "additional assumptions.")
    return "\n".join(lines) + "\n"


def total_contract_value(price_resolution: list[dict]) -> dict:
    """Deterministic grand total -- same computation the quote-accuracy eval
    uses as ground truth (run_quote_accuracy_eval.py: sum extended_quoted_cad,
    "or 0" so unpriced/tavily-no-price rows contribute nothing rather than
    biasing the total). Summed once from the raw rows rather than from the
    already-rounded per-section subtotals in _render_cost_section, so
    rounding compounds at most once, not once per section."""
    total = round(sum(r.get("extended_quoted_cad") or 0 for r in price_resolution), 2)
    excluded = [r.get("description") or r.get("category") or r.get("takeoff_line_ref")
               for r in price_resolution if not r.get("extended_quoted_cad")]
    return {"total_contract_value_cad": total, "excluded_unpriced_lines": excluded}


def _render_code_verifications(codes_checklist: dict) -> str:
    """Checklist items the codes stage marked "verify_on_site"/
    "informational" are attention checks, not billable work -- they have no
    item/trade/allowance and were never meant to reach price_resolution at
    all (see nodes._drop_non_line_item_code_refs, which now strips any
    takeoff line the model created for one before pricing). Surfaced here
    instead, deterministically, so this information isn't simply lost --
    the estimator still needs to see "verify ceiling height on site," just
    not as a line asking for a dollar figure."""
    items = [i for i in (codes_checklist or {}).get("items") or []
            if i.get("action") != "line_item"]
    lines = ["## 25. On-Site Verifications Required", "",
             "Not priced -- these need the estimator's attention/confirmation, "
             "not a dollar figure (§5.15)."]
    if not items:
        lines.append("- None identified for this project.")
    else:
        for i in items:
            citation = f" ({i['citation']})" if i.get("citation") else ""
            lines.append(f"- {i.get('requirement', '')}{citation}")
    return "\n".join(lines) + "\n"


def _render_citations(retrieved: dict) -> str:
    seen: set[tuple[str, str]] = set()
    lines = ["## 24. Citations Appendix", ""]
    for doc_type, chunks in (retrieved or {}).items():
        for c in chunks:
            key = (doc_type, c.get("citation", ""))
            if key in seen or not key[1]:
                continue
            seen.add(key)
            lines.append(f"- [{doc_type}] {c['citation']}")
    if len(lines) == 2:
        lines.append("- No retrieved citations for this draft.")
    return "\n".join(lines) + "\n"


def render_draft(state: dict, narrative: "schemas.DraftNarrative") -> str:
    """Assemble the complete draft document. Everything except
    `narrative` (project summary + pricing confidence, the one remaining
    LLM call) is computed here from state -- see module docstring."""
    slots = state.get("slots") or {}
    price_resolution = state.get("price_resolution") or []
    takeoff = state.get("takeoff") or {}
    retrieved = state.get("retrieved") or {}
    tier = _resolve_tier(slots)

    rows_by_section: dict[int, list[dict]] = {n: [] for n, _ in quote_sections.WORK_CATEGORY_SECTIONS}
    for row in price_resolution:
        number, _ = _row_section(row)
        rows_by_section.setdefault(number, []).append(row)

    sections_md = []
    subtotal_table_rows = []
    for number, heading in quote_sections.WORK_CATEGORY_SECTIONS:
        body, subtotal = _render_cost_section(number, heading, rows_by_section.get(number, []), slots)
        sections_md.append(body)
        post_hst = round(subtotal * (1 + settings.hst_rate), 2)
        subtotal_table_rows.append(f"| {number}. {heading} | {_fmt_money(subtotal)} | {_fmt_money(post_hst)} |")

    tcv = total_contract_value(price_resolution)
    grand_pre_hst = tcv["total_contract_value_cad"]
    grand_post_hst = round(grand_pre_hst * (1 + settings.hst_rate), 2)
    excluded = tcv["excluded_unpriced_lines"]

    subtotal_table = ("## 17. Category Subtotal (pre-HST + post-HST)\n\n"
                      "| Category | Pre-HST | Post-HST |\n|---|---|---|\n"
                      + "\n".join(subtotal_table_rows) + "\n")

    total_block = (f"## 18. Total Contract Value (pre-HST + post-HST)\n\n"
                  f"**Pre-HST: {_fmt_money(grand_pre_hst)}**\n\n"
                  f"**Post-HST ({settings.hst_rate * 100:g}%): {_fmt_money(grand_post_hst)}**\n")
    if excluded:
        total_block += ("\nThis total excludes the following unpriced lines and is not "
                        f"yet final: {', '.join(str(x) for x in excluded)}\n")

    doc = [
        _render_cover(state, narrative),
        _render_project_summary(narrative),
        *sections_md,
        subtotal_table,
        total_block,
        _render_allowances_table(tier),
        _render_milestones(slots),
        _render_exclusions(),
        _render_terms(),
        _render_assumptions(slots, takeoff.get("assumptions") or []),
        _render_citations(retrieved),
        _render_code_verifications(state.get("codes_checklist") or {}),
    ]
    return "\n".join(doc)
