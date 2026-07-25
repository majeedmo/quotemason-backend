"""Tier-based material allowances — a third deterministic pricing source
alongside materials.py (flat market price) and labor.py (installed labor).

corpus/guidelines/material-allowances-DRAFT-v0.csv is real, GROUNDED data
from actual quotes, but its three tier columns are free-text cells mixing a
$ figure with a spec note ("2.00 (7-8mm)", "500 (wooden 24-30in)") or, for
many rows, no $ figure at all ("Shaker thermofoil MDF", "one-piece") — pure
finish specs the takeoff/draft stages already reference narratively, not
something this module can price.

Precedence when both exist: a takeoff line's allowance_item is tried here
first (the tier-accurate source); a spec-only cell at that tier falls back
to materials.py under the same (category, item) key (see price_fill_node).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import CORPUS_DIR

log = logging.getLogger(__name__)

ALLOWANCES_CSV = CORPUS_DIR / "guidelines" / "material-allowances-DRAFT-v0.csv"

# Rule 1: the cell BEGINS with a number (optionally a "$low-high" range) —
# covers "30", "2.00-2.50", "2.00 (7-8mm)", "500 (wooden 24-30in)" (the
# parenthetical's own numbers are never reached since the range group only
# matches immediately after the first number, not after a space+paren).
_LEADING_NUMBER = re.compile(r"^\$?(\d+(?:\.\d+)?)\s*(?:[-–]\s*\$?(\d+(?:\.\d+)?))?")
# Rule 2 (fallback): a trailing "... to $N" phrasing — covers "quartz to 45".
_TO_NUMBER = re.compile(r"\bto\s+\$?(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class AllowanceRow:
    category: str
    item: str
    unit: str
    essential: str
    superior: str
    supreme: str
    status: str
    source: str = ""


def parse_tier_value(cell: str) -> tuple[float, float] | None:
    """A tier cell's $ figure as (low, high), or None when the cell is a
    pure spec note with no parseable number (e.g. "Shaker thermofoil MDF")."""
    cell = (cell or "").strip()
    if not cell:
        return None
    m = _LEADING_NUMBER.match(cell)
    if m:
        low = float(m.group(1))
        high = float(m.group(2)) if m.group(2) else low
        return (low, high)
    m = _TO_NUMBER.search(cell)
    if m:
        val = float(m.group(1))
        return (val, val)
    return None


def load_allowances(path: Path | None = None) -> dict[tuple[str, str], AllowanceRow]:
    """Parse the sheet keyed by (category, item). Missing file or malformed
    row degrades to empty/skip, never raises — pricing must not block
    drafting."""
    path = path or ALLOWANCES_CSV
    if not path.exists():
        log.warning("allowances sheet not found at %s — allowance pricing disabled", path)
        return {}
    rows: dict[tuple[str, str], AllowanceRow] = {}
    with open(path, newline="") as f:
        for lineno, r in enumerate(csv.DictReader(f), start=2):
            try:
                row = AllowanceRow(
                    category=r["category"].strip(),
                    item=r["item"].strip(),
                    unit=r["unit"].strip(),
                    essential=r["ESSENTIAL"].strip(),
                    superior=r["SUPERIOR"].strip(),
                    supreme=r["SUPREME"].strip(),
                    status=r["status"].strip(),
                    source=(r.get("source") or "").strip(),
                )
            except (KeyError, AttributeError) as e:
                log.warning("allowances sheet %s line %d skipped: %s", path.name, lineno, e)
                continue
            rows[(row.category, row.item)] = row
    return rows


@lru_cache(maxsize=1)
def _sheet() -> dict[tuple[str, str], AllowanceRow]:
    return load_allowances()


def lookup(category: str, item: str) -> AllowanceRow | None:
    return _sheet().get((category.strip(), item.strip()))


def tier_range(row: AllowanceRow, tier: str) -> tuple[float, float] | None:
    """The (low, high) $ figure for a tier, or None if that tier's cell is
    spec-only, OR the row's unit isn't a currency unit at all. Rows with
    unit "spec" or "max_count" describe a size/finish/quantity cap, never
    a price, regardless of whether the cell text happens to start with a
    digit — "36x30 sliding" (egress_window, unit "spec") and "40"
    (pot_lights, unit "max_count") are dimensions/counts, not dollars.
    `tier` is case-insensitive (ESSENTIAL/SUPERIOR/SUPREME)."""
    if not row.unit.endswith("_cad"):
        return None
    cell = {"essential": row.essential, "superior": row.superior,
           "supreme": row.supreme}.get((tier or "").strip().lower())
    if cell is None:
        return None
    return parse_tier_value(cell)


def quoted_value(row: AllowanceRow, tier: str) -> float | None:
    """Point estimate for a tier — midpoint of the range (no size axis
    here, same policy as materials.py)."""
    rng = tier_range(row, tier)
    return None if rng is None else (rng[0] + rng[1]) / 2.0
