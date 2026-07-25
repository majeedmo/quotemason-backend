"""Allowance sheet parsing tests — no network, no API keys."""

import pytest

from app.pricing import allowances
from app.pricing.allowances import (AllowanceRow, load_allowances,
                                    parse_tier_value, quoted_value,
                                    tier_range)

_HEADER = "category,item,unit,ESSENTIAL,SUPERIOR,SUPREME,status,source\n"


def _write(tmp_path, body):
    p = tmp_path / "allowances.csv"
    p.write_text(_HEADER + body)
    return p


def test_parse_tier_value_single_and_range_and_parenthetical_notes():
    assert parse_tier_value("30") == (30.0, 30.0)
    assert parse_tier_value("2.00-2.50") == (2.0, 2.5)
    assert parse_tier_value("2.00 (7-8mm)") == (2.0, 2.0)
    assert parse_tier_value("500 (wooden 24-30in)") == (500.0, 500.0)
    assert parse_tier_value("quartz to 45") == (45.0, 45.0)


def test_parse_tier_value_spec_only_returns_none():
    assert parse_tier_value("Shaker thermofoil MDF") is None
    assert parse_tier_value("one-piece") is None
    assert parse_tier_value("") is None
    assert parse_tier_value(None) is None


def test_tier_range_gates_on_currency_unit_not_cell_shape():
    """A leading digit in a spec cell (a dimension or a count) must never
    be mistaken for a dollar figure."""
    egress = AllowanceRow(category="windows", item="egress_window", unit="spec",
                          essential="36x30 sliding", superior="30x30 egress",
                          supreme="48x42 + 30x30 enlarged egress", status="GROUNDED")
    assert tier_range(egress, "essential") is None
    assert tier_range(egress, "superior") is None

    pot_lights = AllowanceRow(category="electrical", item="pot_lights", unit="max_count",
                              essential="40", superior="40", supreme="40", status="GROUNDED")
    assert tier_range(pot_lights, "essential") is None


def test_tier_range_priced_row_resolves_by_tier_case_insensitive():
    row = AllowanceRow(category="kitchen", item="quartz_countertop", unit="per_sqft_cad",
                       essential="30", superior="45", supreme="45", status="GROUNDED")
    assert tier_range(row, "ESSENTIAL") == (30.0, 30.0)
    assert tier_range(row, "Superior") == (45.0, 45.0)
    assert tier_range(row, "unknown_tier") is None


def test_tier_range_mixed_priced_and_spec_only_tiers():
    """vanity: ESSENTIAL has a real $ figure, SUPERIOR/SUPREME are spec-only
    (upgrade descriptions with no number) — the real corpus row, verified."""
    row = AllowanceRow(category="bathroom", item="vanity", unit="per_unit_cad",
                       essential="500 (wooden 24-30in)", superior="floating + quartz top",
                       supreme="floating + quartz top + sconces", status="GROUNDED")
    assert tier_range(row, "essential") == (500.0, 500.0)
    assert tier_range(row, "superior") is None
    assert tier_range(row, "supreme") is None


def test_quoted_value_is_midpoint_of_range():
    row = AllowanceRow(category="flooring", item="lvp", unit="per_sqft_cad",
                       essential="2.00", superior="2.00-2.50", supreme="2.50", status="GROUNDED")
    assert quoted_value(row, "essential") == 2.0
    assert quoted_value(row, "superior") == 2.25
    assert quoted_value(row, "supreme") == 2.5


def test_missing_file_returns_empty_never_raises(tmp_path):
    assert load_allowances(tmp_path / "nope.csv") == {}


def test_malformed_rows_skipped_good_rows_kept(tmp_path):
    p = _write(tmp_path,
               "kitchen,quartz_countertop,per_sqft_cad,30,45,45,GROUNDED,real quotes\n"
               "bathroom,vanity,per_unit_cad,500\n")  # short row -> missing fields are None
    sheet = load_allowances(p)
    assert set(sheet) == {("kitchen", "quartz_countertop")}


def test_lookup_reads_committed_repo_sheet():
    allowances._sheet.cache_clear()
    try:
        row = lookup_via_repo("kitchen", "quartz_countertop")
        assert row is not None
        assert tier_range(row, "essential") == (30.0, 30.0)
        # the two known dimension/count traps stay unpriced against the real file
        egress = lookup_via_repo("windows", "egress_window")
        assert tier_range(egress, "essential") is None
        pot_lights = lookup_via_repo("electrical", "pot_lights")
        assert tier_range(pot_lights, "essential") is None
    finally:
        allowances._sheet.cache_clear()


def lookup_via_repo(category, item):
    from app.pricing.allowances import lookup
    return lookup(category, item)


@pytest.fixture(autouse=True)
def _no_cache_pollution():
    yield
    allowances._sheet.cache_clear()
