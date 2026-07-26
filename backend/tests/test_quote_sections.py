"""app/pricing/quote_sections.py -- the takeoff-category -> quote-document
section mapping (schema-only for now; no renderer consumes it yet)."""

from typing import get_args

from app.agent.schemas import TakeoffCategory
from app.pricing import quote_sections


def test_section_for_known_category_maps_to_its_documented_section():
    assert quote_sections.section_for("bathroom") == (7, "Bathroom(s)")
    assert quote_sections.section_for("windows") == (4, "Separate Entrance & Windows")


def test_section_for_unmapped_category_falls_back_to_misc():
    assert quote_sections.section_for("not_a_real_category") == quote_sections.MISC_SECTION


def test_work_category_sections_are_sequential_and_match_guideline_count():
    numbers = [n for n, _ in quote_sections.WORK_CATEGORY_SECTIONS]
    assert numbers == list(range(2, 17))  # §2-16, 15 headings incl. Demolition & Site Prep
    assert quote_sections.WORK_CATEGORY_SECTIONS[-1] == quote_sections.MISC_SECTION


def test_every_takeoff_category_has_an_explicit_section_mapping():
    """Every value the takeoff LLM is allowed to emit (TakeoffCategory) must
    resolve to a real section, not silently land in Misc by omission --
    "code_required" is the one deliberate exception (see the CSV's note)."""
    for cat in get_args(TakeoffCategory):
        section = quote_sections.section_for(cat)
        if cat == "code_required":
            assert section == quote_sections.MISC_SECTION
        else:
            assert section != quote_sections.MISC_SECTION, (
                f"category {cat!r} has no explicit row in "
                "quote-section-map-DRAFT-v0.csv")
