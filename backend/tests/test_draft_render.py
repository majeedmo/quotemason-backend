"""app/agent/draft_render.py -- the deterministic renderer that replaced
the old free-form LLM-authored draft document. No network, no API keys."""

from app.agent.draft_render import render_draft, total_contract_value
from app.agent.schemas import DraftNarrative
from app.pricing import quote_sections

NARRATIVE = DraftNarrative(
    project_summary="A 900 sqft finished basement conversion.",
    pricing_confidence="MEDIUM",
    confidence_reasons=["comparable project P19 is a close match"])


def _state(price_resolution=None, slots=None, flags=None, takeoff=None, retrieved=None):
    return {
        "slots": {"scope": "finished basement", "gfa_sqft": 900,
                 "package_tier_budget": "ESSENTIAL tier", "cold_room": "none",
                 "separate_entrance": "exists, no new construction",
                 "property_location": "1031 One Street, Cambridge, ON",
                 **(slots or {})},
        "flags": flags or [],
        "price_resolution": price_resolution or [],
        "takeoff": takeoff or {"assumptions": []},
        "retrieved": retrieved or {},
    }


def test_every_work_category_section_present_even_with_no_rows():
    draft = render_draft(_state(), NARRATIVE)
    for number, heading in quote_sections.WORK_CATEGORY_SECTIONS:
        assert f"## {number}. {heading}" in draft, f"missing section {number} {heading}"


def test_empty_section_shows_zero_and_a_reason_not_just_a_blank():
    draft = render_draft(_state(), NARRATIVE)
    # Demolition & Site Prep has no rows in this fixture -- must show $0.00 + reason
    idx = draft.index("## 3. Demolition & Site Prep")
    chunk = draft[idx:idx + 200]
    assert "$0.00" in chunk and "no items identified" in chunk


def test_cold_room_none_renders_deterministic_na_reason_without_llm():
    draft = render_draft(_state(slots={"cold_room": "none"}), NARRATIVE)
    idx = draft.index("## 14. Cold Storage")
    chunk = draft[idx:idx + 200]
    assert "not applicable" in chunk and "cold_room" in chunk


def test_separate_entrance_already_exists_renders_matching_na_reason():
    draft = render_draft(_state(slots={"separate_entrance": "exists, no new construction"}), NARRATIVE)
    idx = draft.index("## 4. Separate Entrance & Windows")
    chunk = draft[idx:idx + 250]
    assert "no new construction" in chunk


def test_priced_rows_land_in_their_mapped_section_with_source_shown():
    rows = [
        {"category": "flooring", "description": "LVP flooring", "quantity": 900,
         "unit": "sqft", "extended_quoted_cad": 5000.0, "price_source": "price_sheet",
         "source_detail": "supplier list (updated 2026-07-10)"},
        {"category": "bathroom", "description": "Toilet", "quantity": 1, "unit": "each",
         "extended_quoted_cad": 475.0, "price_source": "price_sheet",
         "source_detail": "web market research 2026"},
    ]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    flooring_idx = draft.index("## 6. Flooring & Stairs")
    assert "LVP flooring" in draft[flooring_idx:flooring_idx + 400]
    assert "$5,000.00" in draft[flooring_idx:flooring_idx + 400]
    assert "supplier list" in draft[flooring_idx:flooring_idx + 400]
    bathroom_idx = draft.index("## 7. Bathroom(s)")
    assert "Toilet" in draft[bathroom_idx:bathroom_idx + 400]


def test_unpriced_row_shows_estimator_to_price_and_its_note():
    rows = [{"category": "electrical", "description": "Panel upgrade", "quantity": 1,
            "unit": "each", "price_source": "unpriced",
            "note": "no material or labor key on this line — estimator to price"}]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    idx = draft.index("## 11. Electrical")
    chunk = draft[idx:idx + 400]
    assert "estimator to price" in chunk


def test_multiple_instances_of_a_category_repeat_the_section_heading():
    """2 bathrooms -> 2 separate 'Bathroom(s)' heading blocks, not merged."""
    rows = [
        {"category": "bathroom", "description": "Toilet", "quantity": 1, "unit": "each",
         "extended_quoted_cad": 475.0, "price_source": "price_sheet",
         "source_detail": "x", "instance": "bathroom_1"},
        {"category": "bathroom", "description": "Vanity", "quantity": 1, "unit": "each",
         "extended_quoted_cad": 500.0, "price_source": "price_sheet",
         "source_detail": "x", "instance": "bathroom_2"},
    ]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    assert draft.count("Bathroom(s) — Bathroom 1") == 1
    assert draft.count("Bathroom(s) — Bathroom 2") == 1
    b1 = draft.index("Bathroom(s) — Bathroom 1")
    b2 = draft.index("Bathroom(s) — Bathroom 2")
    assert "Toilet" in draft[b1:b2]
    assert "Vanity" in draft[b2:b2 + 400]


def test_omission_placeholder_routes_to_its_real_section_not_misc():
    rows = [{"category": "verifier_flagged", "description": "Possible missing scope: kitchen",
            "quantity": 0, "unit": "lump_sum", "takeoff_line_ref": "omission-kitchen",
            "price_source": "unpriced", "note": "estimator to confirm scope"}]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    idx = draft.index("## 8. Wet Bar/Kitchenette/Kitchen")
    chunk = draft[idx:idx + 400]
    assert "Possible missing scope: kitchen" in chunk
    # must NOT have landed in Misc instead
    misc_idx = draft.index("## 16. Misc")
    assert "kitchen" not in draft[misc_idx:misc_idx + 200].lower()


def test_omission_placeholder_joins_the_sections_one_real_instance():
    """Live bug (2026-07-26): a single-bathroom project's verifier-injected
    omission placeholder has no `instance` of its own (it isn't tied to a
    real takeoff line), so it rendered as a spurious bare "Bathroom(s)"
    heading alongside "Bathroom(s) — Bathroom 1" for the priced lines --
    two headings for one physical bathroom. It must join the section's sole
    real instance instead."""
    rows = [
        {"category": "bathroom", "description": "Toilet", "quantity": 1, "unit": "each",
         "extended_quoted_cad": 475.0, "price_source": "price_sheet",
         "source_detail": "x", "instance": "bathroom_1"},
        {"category": "verifier_flagged", "description": "Possible missing scope: bathroom_rough_in",
         "quantity": 0, "unit": "lump_sum", "takeoff_line_ref": "omission-bathroom_rough_in",
         "price_source": "unpriced", "note": "estimator to confirm scope"},
    ]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    assert draft.count("## 7. Bathroom(s)") == 1  # not also a bare, unsuffixed heading
    idx = draft.index("## 7. Bathroom(s) — Bathroom 1")
    chunk = draft[idx:idx + 500]
    assert "Toilet" in chunk and "Possible missing scope" in chunk


def test_category_subtotal_table_sums_to_total_contract_value():
    rows = [
        {"category": "flooring", "description": "LVP", "extended_quoted_cad": 5000.0,
         "price_source": "price_sheet", "quantity": 900, "unit": "sqft"},
        {"category": "bathroom", "description": "Toilet", "extended_quoted_cad": 475.0,
         "price_source": "price_sheet", "quantity": 1, "unit": "each"},
    ]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    tcv = total_contract_value(rows)
    assert f"${tcv['total_contract_value_cad']:,.2f}" in draft
    assert "## 17. Category Subtotal (pre-HST + post-HST)" in draft
    assert "## 18. Total Contract Value (pre-HST + post-HST)" in draft


def test_total_contract_value_post_hst_uses_configured_rate():
    from app.config import settings
    rows = [{"category": "flooring", "description": "LVP", "extended_quoted_cad": 100.0,
            "price_source": "price_sheet", "quantity": 1, "unit": "each"}]
    draft = render_draft(_state(price_resolution=rows), NARRATIVE)
    expected_post = round(100.0 * (1 + settings.hst_rate), 2)
    idx = draft.index("## 18. Total Contract Value")
    assert f"${expected_post:,.2f}" in draft[idx:idx + 300]


def test_flags_render_as_estimator_review_required_block():
    flags = [{"condition": "ceiling", "flag_text": "Feasibility: verify clear height"}]
    draft = render_draft(_state(flags=flags), NARRATIVE)
    assert "ESTIMATOR REVIEW REQUIRED" in draft
    assert "Feasibility: verify clear height" in draft


def test_project_summary_and_confidence_come_from_narrative_verbatim():
    draft = render_draft(_state(), NARRATIVE)
    assert NARRATIVE.project_summary in draft
    assert "MEDIUM" in draft
    assert "comparable project P19 is a close match" in draft


def test_assumptions_include_unknown_slots_and_takeoff_assumptions():
    draft = render_draft(_state(slots={"kitchen": "unknown"},
                                takeoff={"assumptions": ["assumed 9ft ceiling"]}), NARRATIVE)
    idx = draft.index("## 23. Assumptions")
    chunk = draft[idx:]
    assert "kitchen" in chunk and "unknown" in chunk
    assert "assumed 9ft ceiling" in chunk


def test_citations_appendix_dedupes_and_lists_retrieved_citations():
    retrieved = {"building_code": [{"citation": "OBC 9.9.10", "text": "..."},
                                   {"citation": "OBC 9.9.10", "text": "dup"}],
                "zoning_bylaw": [{"citation": "By-law 26-007 §4.19", "text": "..."}]}
    draft = render_draft(_state(retrieved=retrieved), NARRATIVE)
    idx = draft.index("## 24. Citations Appendix")
    chunk = draft[idx:]
    assert chunk.count("OBC 9.9.10") == 1
    assert "By-law 26-007" in chunk


def test_allowances_table_reflects_resolved_tier_label():
    draft = render_draft(_state(slots={"package_tier_budget": "SUPERIOR tier"}), NARRATIVE)
    idx = draft.index("## 19. Allowances Table")
    assert "SUPERIOR" in draft[idx:idx + 200]
