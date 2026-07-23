"""Contractor labor rate sheet — trade-level cost lookup for price_fill.

Unlike the material price sheet (materials.py), this reads
corpus/guidelines/labor-rates-DRAFT-v0.csv directly: that file stays
dual-purpose — still RAG-ingested (builder_guideline, for the drafter's
narrative context) AND tool-read here for deterministic price resolution.
Rows are keyed by (trade, job_size_band); many trades use "any" (their rate
doesn't vary with project size) while others are banded small/medium/large,
so lookup() tries the exact band before falling back to "any".

status distinguishes three states (not a staleness gate — labor rates don't
carry updated_at the way material prices do):
  VERIFIED                the owner has confirmed the rate as final
  VERIFIED_SITE_DEPENDENT confirmed as a range, but site conditions (soil,
                          depth, access) can move the real cost — quoted
                          with a "confirm on site" caveat, not "unverified"
  PLACEHOLDER_OWNER_VERIFY not yet reviewed — price_fill marks amounts
                          derived from these rows "rate unverified"
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import CORPUS_DIR

log = logging.getLogger(__name__)

LABOR_RATES_CSV = CORPUS_DIR / "guidelines" / "labor-rates-DRAFT-v0.csv"


@dataclass(frozen=True)
class LaborRow:
    trade: str
    job_size_band: str
    unit: str
    rate_low_cad: float
    rate_high_cad: float
    includes: str
    status: str
    notes: str = ""


def load_labor_rates(path: Path | None = None) -> dict[tuple[str, str], LaborRow]:
    """Parse the sheet keyed by (trade, job_size_band). A missing file or a
    malformed row never raises — labor pricing degrades to 'unpriced'; it
    must not block drafting."""
    path = path or LABOR_RATES_CSV
    if not path.exists():
        log.warning("labor rate sheet not found at %s — labor pricing disabled", path)
        return {}
    rows: dict[tuple[str, str], LaborRow] = {}
    with open(path, newline="") as f:
        for lineno, r in enumerate(csv.DictReader(f), start=2):
            try:
                row = LaborRow(
                    trade=r["trade"].strip(),
                    job_size_band=r["job_size_band"].strip(),
                    unit=r["unit"].strip(),
                    rate_low_cad=float(r["rate_low_cad"]),
                    rate_high_cad=float(r["rate_high_cad"]),
                    includes=r["includes"].strip(),
                    status=r["status"].strip(),
                    notes=(r.get("notes") or "").strip(),
                )
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                log.warning("labor sheet %s line %d skipped: %s", path.name, lineno, e)
                continue
            rows[(row.trade, row.job_size_band)] = row
    return rows


@lru_cache(maxsize=1)
def _sheet() -> dict[tuple[str, str], LaborRow]:
    return load_labor_rates()


def lookup(trade: str, job_size_band: str | None) -> LaborRow | None:
    """Exact (trade, job_size_band) first, then (trade, "any") for trades
    whose rate doesn't vary with project size. job_size_band may be None
    (GFA unknown) — falls straight to the "any" attempt."""
    sheet = _sheet()
    if job_size_band:
        row = sheet.get((trade.strip(), job_size_band.strip()))
        if row is not None:
            return row
    return sheet.get((trade.strip(), "any"))


def job_size_band(gfa_sqft: float | None) -> str | None:
    """Deterministic band from the CSV's own breakpoints: <500 small,
    500-1000 inclusive medium, >1000 large (capped there above 2000 sqft —
    basements rarely exceed it, and there is no larger band to fall to).
    None when GFA is unknown — callers must not guess a default band, since
    that silently picks a rate the project may not match."""
    if gfa_sqft is None:
        return None
    if gfa_sqft < 500:
        return "small_lt_500sqft"
    if gfa_sqft <= 1000:
        return "medium_500_1000sqft"
    return "large_1000_2000sqft"


def is_rate_unverified(row: LaborRow) -> bool:
    return row.status == "PLACEHOLDER_OWNER_VERIFY"


def is_site_dependent(row: LaborRow) -> bool:
    return row.status == "VERIFIED_SITE_DEPENDENT"
