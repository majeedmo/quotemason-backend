"""Labor rate sheet loader tests — no network, no API keys."""

import pytest

from app.pricing import labor
from app.pricing.labor import (LaborRow, job_size_band, load_labor_rates,
                               lookup, quoted_rate, size_fraction)

_HEADER = "trade,job_size_band,unit,rate_low_cad,rate_high_cad,includes,status,notes\n"


def _write(tmp_path, body):
    p = tmp_path / "labor.csv"
    p.write_text(_HEADER + body)
    return p


def test_parses_rows_keyed_by_trade_and_band(tmp_path):
    p = _write(tmp_path,
               "framing,small_lt_500sqft,per_sqft_floor,4.50,6.50,studs,VERIFIED,\n"
               "concrete_cutting_window,any,per_opening,1800,3200,cut+lintel,VERIFIED,\n")
    sheet = load_labor_rates(p)
    assert set(sheet) == {("framing", "small_lt_500sqft"),
                          ("concrete_cutting_window", "any")}
    row = sheet[("framing", "small_lt_500sqft")]
    assert row.rate_low_cad == 4.50 and row.rate_high_cad == 6.50
    assert row.status == "VERIFIED"


def test_missing_file_returns_empty_never_raises(tmp_path):
    assert load_labor_rates(tmp_path / "nope.csv") == {}


def test_malformed_rows_skipped_good_rows_kept(tmp_path):
    p = _write(tmp_path,
               "framing,small_lt_500sqft,per_sqft_floor,notanumber,6.50,studs,VERIFIED,\n"
               "painting,small_lt_500sqft,lump_sum,2500,4500,paint,VERIFIED,\n")
    sheet = load_labor_rates(p)
    assert set(sheet) == {("painting", "small_lt_500sqft")}


def test_lookup_falls_back_to_any_band(tmp_path, monkeypatch):
    p = _write(tmp_path, "concrete_cutting_window,any,per_opening,1800,3200,cut,VERIFIED,\n")
    monkeypatch.setattr(labor, "_sheet", lambda: load_labor_rates(p))
    assert lookup("concrete_cutting_window", "small_lt_500sqft") is not None
    assert lookup("concrete_cutting_window", None) is not None
    assert lookup("nonexistent_trade", "any") is None


def test_lookup_prefers_exact_band_over_any(tmp_path, monkeypatch):
    p = _write(tmp_path,
               "framing,small_lt_500sqft,per_sqft_floor,4.50,6.50,studs,VERIFIED,\n"
               "framing,any,per_sqft_floor,99,99,fallback-should-not-hit,VERIFIED,\n")
    monkeypatch.setattr(labor, "_sheet", lambda: load_labor_rates(p))
    row = lookup("framing", "small_lt_500sqft")
    assert row.rate_low_cad == 4.50  # exact band wins over "any"


def test_job_size_band_boundaries():
    assert job_size_band(None) is None
    assert job_size_band(499) == "small_lt_500sqft"
    assert job_size_band(500) == "medium_500_1000sqft"
    assert job_size_band(1000) == "medium_500_1000sqft"
    assert job_size_band(1001) == "large_1000_2000sqft"
    assert job_size_band(5000) == "large_1000_2000sqft"  # capped, not out-of-range


def test_repo_sheet_loads_and_status_values_are_known():
    labor._sheet.cache_clear()
    try:
        sheet = labor._sheet()
        assert sheet  # non-empty — the real CSV parses cleanly
        statuses = {row.status for row in sheet.values()}
        assert statuses <= {"VERIFIED", "VERIFIED_SITE_DEPENDENT", "PLACEHOLDER_OWNER_VERIFY"}
        # the two site-dependent rows flagged during review are wired through
        assert labor.is_site_dependent(sheet[("excavation_below_grade_entrance", "any")])
        assert labor.is_site_dependent(sheet[("window_well_install", "any")])
        # a normal row is plain VERIFIED, not unverified or site-dependent
        normal = sheet[("framing", "small_lt_500sqft")]
        assert not labor.is_rate_unverified(normal)
        assert not labor.is_site_dependent(normal)
    finally:
        labor._sheet.cache_clear()


# --- point-estimate policy: collapsing (low, high) to a single quoted number -

def _row(**overrides):
    defaults = dict(trade="framing", job_size_band="small_lt_500sqft",
                    unit="per_sqft_floor", rate_low_cad=4.0, rate_high_cad=6.0,
                    includes="x", status="VERIFIED", notes="")
    return LaborRow(**{**defaults, **overrides})


def test_size_fraction_within_band_clamped_and_none_cases():
    assert size_fraction(None, "small_lt_500sqft") is None
    assert size_fraction(400, "any") is None
    assert size_fraction(0, "small_lt_500sqft") == 0.0
    assert size_fraction(500, "small_lt_500sqft") == 1.0
    assert size_fraction(250, "small_lt_500sqft") == 0.5
    assert size_fraction(1500, "large_1000_2000sqft") == 0.5
    assert size_fraction(5000, "large_1000_2000sqft") == 1.0  # clamped, not >1


def test_quoted_rate_lump_sum_scales_toward_high_near_band_top():
    row = _row(unit="lump_sum", rate_low_cad=6000, rate_high_cad=10000)
    assert quoted_rate(row, 0) == 6000     # band bottom edge -> low
    assert quoted_rate(row, 500) == 10000  # band top edge -> high
    assert quoted_rate(row, 250) == 8000   # band midpoint -> rate midpoint


def test_quoted_rate_per_unit_scales_toward_low_near_band_top_reversed():
    row = _row(unit="per_sqft_floor", rate_low_cad=4.50, rate_high_cad=6.50)
    assert quoted_rate(row, 0) == 6.50    # band bottom edge -> HIGH (reversed)
    assert quoted_rate(row, 500) == 4.50  # band top edge -> LOW (reversed)
    assert quoted_rate(row, 250) == 5.50  # band midpoint -> rate midpoint


def test_quoted_rate_any_band_is_always_midpoint_regardless_of_gfa():
    row = _row(job_size_band="any", unit="per_opening",
              rate_low_cad=1800, rate_high_cad=3200)
    assert quoted_rate(row, 50) == 2500
    assert quoted_rate(row, 1999) == 2500
    assert quoted_rate(row, None) == 2500


def test_quoted_rate_site_dependent_always_midpoint_even_near_band_edges():
    row = _row(unit="lump_sum", rate_low_cad=8000, rate_high_cad=15000,
              status="VERIFIED_SITE_DEPENDENT")
    # would otherwise scale toward the high end near the band top — must not
    assert quoted_rate(row, 490) == 11500
    assert quoted_rate(row, 10) == 11500


@pytest.fixture(autouse=True)
def _no_cache_pollution():
    yield
    labor._sheet.cache_clear()
