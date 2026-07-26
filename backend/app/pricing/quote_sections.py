"""Canonical quote-document section schema, and the takeoff-line `category`
-> section mapping that will let a deterministic renderer group
price_resolution rows into it.

Every price_resolution row inherits its parent takeoff line's `category`
verbatim (see price_fill_node's `base` dict in app/agent/nodes.py) regardless
of whether it was priced via the material sheet, an allowance, or a labor
rate -- so `category` alone is enough to place a row in its section; there's
no need to separately track which CSV a price came from.

corpus/guidelines/quote-section-map-DRAFT-v0.csv is the config; the
authoritative heading list it must agree with lives in
corpus/guidelines/builder-guidelines-DRAFT-v0.md §2 ("Work categories used
in every quote -- keep these headings, in this order").

Every heading in WORK_CATEGORY_SECTIONS appears on every quote, even at
$0.00 with a stated reason, once a deterministic renderer is wired up
(tracked separately) -- this module only defines the schema and the lookup,
it doesn't render anything yet.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import CORPUS_DIR

SECTION_MAP_CSV = CORPUS_DIR / "guidelines" / "quote-section-map-DRAFT-v0.csv"

# Canonical, ordered work-category sections -- numbers match the rendered
# document (see corpus/guidelines/builder-guidelines-DRAFT-v0.md §2).
# Architectural/Permit (2), Cold Storage (14) and Project Management (15)
# have no takeoff `category` of their own (policy-driven, or slot-driven
# for Cold Storage) -- they're not in SECTION_MAP_CSV, but still always
# appear in the rendered document.
WORK_CATEGORY_SECTIONS: list[tuple[int, str]] = [
    (2, "Architectural/Permit"),
    (3, "Demolition & Site Prep"),
    (4, "Separate Entrance & Windows"),
    (5, "Partitions + Insulation"),
    (6, "Flooring & Stairs"),
    (7, "Bathroom(s)"),
    (8, "Wet Bar/Kitchenette/Kitchen"),
    (9, "Plumbing (Code-Driven)"),
    (10, "Primer + Paint"),
    (11, "Electrical"),
    (12, "HVAC/Gas"),
    (13, "Millwork/Doors + Trim"),
    (14, "Cold Storage"),
    (15, "Project Management"),
    (16, "Misc"),
]

MISC_SECTION = (16, "Misc")


@dataclass(frozen=True)
class SectionMapRow:
    category: str
    section_number: int
    section_heading: str
    notes: str = ""


def load_section_map(path: Path | None = None) -> dict[str, SectionMapRow]:
    """Parse the mapping sheet keyed by takeoff-line `category`. A missing
    file or malformed row never raises -- an unmapped category falls back
    to Misc via section_for(), never a crash mid-pricing/rendering."""
    path = path or SECTION_MAP_CSV
    if not path.exists():
        return {}
    rows: dict[str, SectionMapRow] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                row = SectionMapRow(
                    category=r["category"].strip(),
                    section_number=int(r["section_number"]),
                    section_heading=r["section_heading"].strip(),
                    notes=(r.get("notes") or "").strip())
            except (KeyError, TypeError, ValueError):
                continue
            rows[row.category] = row
    return rows


@lru_cache(maxsize=1)
def _map() -> dict[str, SectionMapRow]:
    return load_section_map()


def section_for(category: str) -> tuple[int, str]:
    """The (section_number, section_heading) a takeoff-line category maps
    to. Falls back to Misc for anything unmapped rather than raising --
    an unmapped category is a config gap to fix, not a reason to crash a
    quote mid-render."""
    row = _map().get((category or "").strip())
    return (row.section_number, row.section_heading) if row else MISC_SECTION
