"""Labor rate sheet loader tests — no network, no API keys."""

import pytest

from app.pricing import labor
from app.pricing.labor import job_size_band, load_labor_rates, lookup

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


@pytest.fixture(autouse=True)
def _no_cache_pollution():
    yield
    labor._sheet.cache_clear()
