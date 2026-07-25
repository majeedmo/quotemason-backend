"""Contractor material price sheet — the pricing tool's source of truth.

The sheet is a committed CSV the owner updates at intervals
(corpus/contractors/<id>/material-prices.csv). Its (category, item) keys
follow the material-allowances CSV vocabulary, so takeoff lines, allowances,
and current prices all join on the same names.

Deliberately NOT ingested as RAG chunks: prices carry a staleness gate that
retrieval cannot express, and the pricing node needs structured rows, not
prose. Rows past settings.price_staleness_days are treated as missing by the
pricing node (web fallback / "estimator to price"), never silently used.
Living outside the corpus/guidelines/ ingestion glob enforces the no-RAG
rule by construction.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.contractor import get_contractor

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceRow:
    category: str
    item: str
    unit: str
    price_low_cad: float
    price_high_cad: float
    updated_at: date
    source: str
    notes: str = ""


def load_price_sheet(path: Path | None = None) -> dict[tuple[str, str], PriceRow]:
    """Parse the sheet keyed by (category, item). A missing file or a
    malformed row never raises — pricing degrades to fallback/'estimator to
    price'; it must not block drafting."""
    path = path or get_contractor().prices_csv
    if not path.exists():
        log.warning("price sheet not found at %s — sheet pricing disabled", path)
        return {}
    rows: dict[tuple[str, str], PriceRow] = {}
    with open(path, newline="") as f:
        for lineno, r in enumerate(csv.DictReader(f), start=2):
            try:
                row = PriceRow(
                    category=r["category"].strip(),
                    item=r["item"].strip(),
                    unit=r["unit"].strip(),
                    price_low_cad=float(r["price_low_cad"]),
                    price_high_cad=float(r["price_high_cad"]),
                    updated_at=datetime.strptime(
                        r["updated_at"].strip(), "%Y-%m-%d").date(),
                    source=r["source"].strip(),
                    notes=(r.get("notes") or "").strip(),
                )
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                log.warning("price sheet %s line %d skipped: %s", path.name, lineno, e)
                continue
            rows[(row.category, row.item)] = row
    return rows


@lru_cache(maxsize=1)
def _sheet() -> dict[tuple[str, str], PriceRow]:
    return load_price_sheet()


def lookup(category: str, item: str) -> PriceRow | None:
    """Current price row for (category, item), or None if not on the sheet.

    Falls back to an item-only match when the exact pair misses but the item
    name is unambiguous on the sheet — the takeoff model sometimes writes a
    close-but-wrong category for an item it copied correctly (e.g. "painting"
    for the sheet's "paint", live 2026-07-25), and losing a whole line's
    material price to that is worse than trusting an unambiguous item name."""
    sheet = _sheet()
    row = sheet.get((category.strip(), item.strip()))
    if row is not None:
        return row
    item = item.strip()
    matches = [r for (_, i), r in sheet.items() if i == item]
    return matches[0] if len(matches) == 1 else None


def is_stale(row: PriceRow, threshold_days: int | None = None,
             today: date | None = None) -> bool:
    """A row older than the threshold must not be quoted from — the pricing
    node falls back (web check when available, else 'estimator to price')."""
    limit = settings.price_staleness_days if threshold_days is None else threshold_days
    return ((today or date.today()) - row.updated_at) > timedelta(days=limit)


def quoted_price(row: PriceRow) -> float:
    """Collapse (price_low_cad, price_high_cad) to the single number the
    draft quotes. Material prices carry no job-size axis (unlike labor
    rates) — always the midpoint."""
    return (row.price_low_cad + row.price_high_cad) / 2.0
