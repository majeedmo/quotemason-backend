"""Price-sheet loader tests — no network, no API keys."""

from datetime import date

import pytest

from app.pricing import materials
from app.pricing.materials import (PriceRow, is_stale, load_price_sheet,
                                   lookup, quoted_price)

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


def test_lookup_falls_back_to_unambiguous_item_when_category_wrong(tmp_path, monkeypatch):
    """Regression: quote #22 (2026-07-25) silently dropped ~$2,700 of paint
    material because the takeoff model wrote category "painting" instead of
    the sheet's "paint" — the item name ("interior_paint") was right, only
    the category was off. An unambiguous item-name match must still price."""
    p = _write(tmp_path, "paint,interior_paint,per_gallon_cad,45,75,2026-07-10,x,\n")
    monkeypatch.setattr(materials, "_sheet", lambda: load_price_sheet(p))
    row = lookup("painting", "interior_paint")
    assert row is not None
    assert row.category == "paint"


def test_lookup_item_fallback_is_skipped_when_ambiguous(tmp_path, monkeypatch):
    """Two categories sharing an item name must not silently pick one --
    ambiguous item-only matches stay None rather than guessing wrong."""
    p = _write(tmp_path,
               "paint,finish,per_gallon_cad,45,75,2026-07-10,x,\n"
               "stairs,finish,lump_sum,500,900,2026-07-10,x,\n")
    monkeypatch.setattr(materials, "_sheet", lambda: load_price_sheet(p))
    assert lookup("flooring", "finish") is None


def test_quoted_price_is_always_the_midpoint():
    """Materials carry no job-size axis (unlike labor rates) — always the
    midpoint, regardless of how large the project is."""
    row = PriceRow(category="c", item="i", unit="u", price_low_cad=4.0,
                   price_high_cad=6.0, updated_at=date(2026, 1, 1), source="s")
    assert quoted_price(row) == 5.0


def test_repo_sheet_is_outside_the_rag_ingestion_glob():
    """The price sheet must never become RAG chunks (staleness gate would be
    bypassed): no ingested chunk may originate from corpus/contractors/."""
    from app.ingestion.loaders import load_all
    assert not [d for d in load_all() if "corpus/contractors/" in d.source_file]


@pytest.fixture(autouse=True)
def _no_cache_pollution():
    yield
    materials._sheet.cache_clear()
