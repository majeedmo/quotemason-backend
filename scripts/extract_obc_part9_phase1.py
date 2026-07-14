# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4.0", "cryptography>=3.1"]
# ///
"""Extract the approved Phase 1 OBC Part 9 sections from the 2024 Building Code
Compendium (corpus/OBC/301880.pdf) into metadata-tagged markdown files.

Run:  uv run scripts/extract_obc_part9_phase1.py

Section list approved 2026-07-12 (see project-brief.md Task 3 item 3). Page
numbers are 1-indexed PDF pages, verified against the compendium the same day.
Output files carry YAML frontmatter matching the RAG metadata schema; the
ingestion pipeline chunks these by article and prefixes section number + title
into each chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

REPO = Path(__file__).resolve().parent.parent
SOURCE_PDF = REPO / "corpus/OBC/301880.pdf"
OUT_DIR = REPO / "corpus/OBC/part9_phase1"

COMMON_META = {
    "jurisdiction": "ontario",
    "doc_type": "building_code",
    "source_version": "2024 Building Code Compendium (O. Reg. 163/24)",
    "effective_date": "2025-01-01",
    "source_file": "corpus/OBC/301880.pdf",
    "source_note": "publications.ontario.ca product 301880",
}


@dataclass
class Extract:
    slug: str
    section_number: str
    title: str
    pages: list[tuple[int, int]]  # inclusive 1-indexed PDF page ranges
    division: str = "B"
    note: str = ""


SECTIONS: list[Extract] = [
    Extract(
        "divA-1.4.1.2-defined-terms",
        "1.4.1.2",
        "Defined Terms",
        [(81, 101)],
        division="A",
        note=(
            "Full defined-terms article. At retrieval time the terms that matter "
            "here: dwelling unit, suite, secondary suite, fire separation, "
            "means of egress, bedroom, basement, exit."
        ),
    ),
    Extract(
        "9.5-rooms-and-spaces",
        "9.5",
        "Design of Areas, Spaces and Doorways",
        [(725, 730)],
        note=(
            "Covers 9.5.1 General, 9.5.3 Ceiling Heights, 9.5.3A-9.5.3F minimum "
            "room areas (Ontario-specific articles), 9.5.4 Hallways, 9.5.5 "
            "Doorway Sizes. 9.5.2 Barrier-Free Design rides along in the page "
            "range."
        ),
    ),
    Extract(
        "9.7-windows",
        "9.7.1-9.7.2",
        "Windows, Doors and Skylights — General and Required Windows",
        [(735, 737)],
        note="Window areas and egress-capable openings; egress sizing itself is 9.9.10.",
    ),
    Extract(
        "9.8-stair-dimensions",
        "9.8.1-9.8.4",
        "Stairs — Application, Stair Dimensions, Configurations, Step Dimensions",
        [(741, 745)],
    ),
    Extract(
        "9.8-handrails-guards",
        "9.8.7-9.8.8",
        "Stairs — Handrails and Guards",
        [(747, 751)],
    ),
    Extract(
        "9.9-egress",
        "9.9.9-9.9.10",
        "Means of Egress — Egress from Dwelling Units and Bedrooms",
        [(764, 766)],
        note="9.9.10 is the basement-bedroom egress-window requirement.",
    ),
    Extract(
        "9.10.9-fire-separations",
        "9.10.9",
        "Fire Separations and Smoke-tight Barriers between Rooms and Spaces",
        [(774, 780)],
        note="Separation requirements between suites — core accessory-unit trigger.",
    ),
    Extract(
        "9.10.13-closures",
        "9.10.13",
        "Doors, Dampers and Other Closures in Fire Separations",
        [(784, 787)],
    ),
    Extract(
        "9.10.19-smoke-alarms",
        "9.10.19",
        "Smoke Alarms",
        [(800, 802)],
    ),
    Extract(
        "9.11-sound-transmission",
        "9.11.1",
        "Sound Transmission — Protection from Airborne Noise",
        [(805, 806)],
        note="STC rating between suites; drives resilient-channel/insulation line items.",
    ),
    Extract(
        "9.31-plumbing-facilities",
        "9.31",
        "Plumbing Facilities",
        [(948, 950)],
        note="9.31.4 Required Facilities and 9.31.6 Service Water Heating per unit.",
    ),
    Extract(
        "9.32.1-ventilation-general",
        "9.32.1-9.32.2",
        "Ventilation — General and Non-Heating-Season Ventilation",
        [(950, 951)],
    ),
    Extract(
        "9.32.3.9-co-alarms",
        "9.32.3.9",
        "Carbon Monoxide Alarms",
        [(959, 960)],
        note="Includes Ontario article 9.32.3.9A. CO alarms moved here from 9.33.4 (2012 OBC).",
    ),
    Extract(
        "9.33.1-heating",
        "9.33.1",
        "Heating and Air-Conditioning — General Requirements",
        [(966, 967)],
    ),
    Extract(
        "9.41-change-of-use",
        "9.41",
        "Additional Requirements for Change of Use",
        [(986, 986)],
        note=(
            "Scope clause (b) — one Group C suite converted into more than one — "
            "is the basement-apartment conversion. Points to Part 11 compensating-"
            "construction articles (11.4.3.x), which are not in Phase 1 scope."
        ),
    ),
]

# Running headers/footers on every compendium page, e.g.
#   "2024 Building Code Compendium 9.5.1.1."  /  "16 Division B – Part 9"
HEADER_PATTERNS = [
    re.compile(r"^\s*(Table\s+[\d.A-Z-]+\s+)?2024 Building Code Compendium.*$"),
    re.compile(r"^\s*\d*\s*Division [AB] – Part \d+\s*\d*\s*$"),
]


def clean_page(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if any(p.match(line) for p in HEADER_PATTERNS):
            continue
        lines.append(line.rstrip())
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def frontmatter(ex: Extract) -> str:
    pages = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in ex.pages)
    fields = {
        **COMMON_META,
        "division": ex.division,
        "part": "9" if ex.division == "B" else "1",
        "section_number": ex.section_number,
        "title": ex.title,
        "pdf_pages": pages,
    }
    body = "\n".join(f"{k}: {v!r}" for k, v in fields.items())
    return f"---\n{body}\n---\n"


def main() -> None:
    reader = PdfReader(SOURCE_PDF)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for ex in SECTIONS:
        chunks = []
        for start, end in ex.pages:
            for i in range(start - 1, end):
                chunks.append(clean_page(reader.pages[i].extract_text() or ""))
        text = "\n\n".join(chunks)
        out = OUT_DIR / f"obc-{ex.slug}.md"
        parts = [frontmatter(ex), f"# OBC {ex.section_number} — {ex.title}\n"]
        if ex.note:
            parts.append(f"> Extraction note: {ex.note}\n")
        parts.append(text + "\n")
        out.write_text("\n".join(parts))
        print(f"wrote {out.relative_to(REPO)}  ({len(text):,} chars)")


if __name__ == "__main__":
    main()
