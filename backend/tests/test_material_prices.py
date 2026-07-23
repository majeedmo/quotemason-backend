"""Price-sheet loader tests — no network, no API keys."""

from datetime import date

import pytest

from app.pricing import materials
from app.pricing.materials import PriceRow, is_stale, load_price_sheet, lookup

_HEADER = "category,item,unit,price_low_cad,price_high_cad,updated_at,source,notes\n"


def _write(tmp_path, body):
    p = tmp_path / "prices.csv"
    p.write_text(_HEADER + body)
    return p


def test_parses_rows_keyed_by_category_item(tmp_path):
    p = _write(tmp_path,
               "flooring,lvp,per_sqft_cad,4.50,7.00,2026-07-10,supplier list,installed\n"
               "kitchen,quartz_countertop,per_sqft_cad,55,95,2026-07-10,supplier list,\n")
    sheet = load_price_sheet(p)
    assert set(sheet) == {("flooring", "lvp"), ("kitchen", "quartz_countertop")}
    row = sheet[("flooring", "lvp")]
    assert row.price_low_cad == 4.50 and row.price_high_cad == 7.00
    assert row.updated_at == date(2026, 7, 10)
    assert row.notes == "installed"


def test_missing_file_returns_empty_never_raises(tmp_path):
    assert load_price_sheet(tmp_path / "nope.csv") == {}


def test_malformed_rows_skipped_good_rows_kept(tmp_path):
    p = _write(tmp_path,
               "flooring,lvp,per_sqft_cad,notanumber,7.00,2026-07-10,x,\n"
               "windows,egress_window,per_opening_cad,3500,6500,July 2026,x,\n"
               "paint,interior_paint,per_gallon_cad,45,75,2026-07-10,x,\n")
    sheet = load_price_sheet(p)
    assert set(sheet) == {("paint", "interior_paint")}


def test_staleness_boundary_exactly_threshold_is_fresh():
    row = PriceRow(category="c", item="i", unit="u", price_low_cad=1,
                   price_high_cad=2, updated_at=date(2026, 4, 19), source="s")
    today = date(2026, 7, 18)  # 90 days later
    assert not is_stale(row, threshold_days=90, today=today)
    assert is_stale(row, threshold_days=89, today=today)


def test_is_stale_defaults_to_settings_threshold(monkeypatch):
    monkeypatch.setattr(materials.settings, "price_staleness_days", 10)
    row = PriceRow(category="c", item="i", unit="u", price_low_cad=1,
                   price_high_cad=2, updated_at=date(2026, 7, 1), source="s")
    assert is_stale(row, today=date(2026, 7, 18))
    assert not is_stale(row, today=date(2026, 7, 5))


def test_lookup_reads_committed_repo_sheet():
    """The real corpus sheet must stay loadable and keep its anchor items —
    the pricing node's three slot-trigger materials."""
    materials._sheet.cache_clear()
    try:
        for key in (("flooring", "lvp"), ("kitchen", "quartz_countertop"),
                    ("windows", "egress_window")):
            row = lookup(*key)
            assert row is not None, key
            assert row.price_low_cad <= row.price_high_cad
        assert lookup("flooring", "unobtainium") is None
    finally:
        materials._sheet.cache_clear()


def test_repo_sheet_is_outside_the_rag_ingestion_glob():
    """The price sheet must never become RAG chunks (staleness gate would be
    bypassed): no ingested chunk may originate from corpus/contractors/."""
    from app.ingestion.loaders import load_all
    assert not [d for d in load_all() if "corpus/contractors/" in d.source_file]


@pytest.fixture(autouse=True)
def _no_cache_pollution():
    yield
    materials._sheet.cache_clear()
