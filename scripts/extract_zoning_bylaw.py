# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4.0", "cryptography>=3.1"]
# ///
"""Extract the Cambridge zoning bylaw (By-law 26-007, Phase 1 Comprehensive
Zoning By-law) into metadata-tagged markdown files, one per Part.

Run:  uv run scripts/extract_zoning_bylaw.py

Source is the December 2025 recommended draft — the text council enacted
2026-02-03 (project-brief.md Task 3 item 4). Ingested with
source_version "26-007 draft (Dec 2025)" so the certified copy cleanly
supersedes it when planning@cambridge.ca delivers one (see
scripts/refresh_bylaw.py for that flow). The companion staff report
(council-report-25-034-PG.pdf) contains no zoning provisions — never extracted.

Page ranges are 1-indexed PDF pages (printed page numbers match PDF pages),
taken from the document's own table of contents. Reserved parts (6, 8-10,
14-16) and Part 17 (enactment clause) hold no provisions and are skipped;
pages 103+ are the zoning maps (image-only, out of scope for text RAG).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

REPO = Path(__file__).resolve().parent.parent
SOURCE_PDF = REPO / "corpus/cambridge-zoning-bylaw/phase1-zoning-bylaw-26-007-draft-dec2025.pdf"
OUT_DIR = REPO / "corpus/cambridge-zoning-bylaw/parts"

COMMON_META = {
    "jurisdiction": "cambridge",
    "doc_type": "zoning_bylaw",
    "source_version": "26-007 draft (Dec 2025)",
    "effective_date": "2026-02-03",
    "source_file": "corpus/cambridge-zoning-bylaw/phase1-zoning-bylaw-26-007-draft-dec2025.pdf",
    "source_note": "City of Cambridge By-law 26-007 (Phase 1 Comprehensive Zoning By-law), "
                   "Dec 2025 recommended draft; enacted 2026-02-03, replaces the residential "
                   "zones/regulations of By-law 150-85",
}


@dataclass
class Extract:
    slug: str
    part: str
    title: str
    pages: tuple[int, int]  # inclusive 1-indexed PDF page range
    note: str = ""


PARTS: list[Extract] = [
    Extract(
        "part1-interpretation-administration",
        "1",
        "Interpretation and Administration",
        (9, 15),
        note=(
            "1.8 Non-Complying Buildings, Structures and Lots — incl. 1.8.1 "
            "replacement/enlargement/repair or renovation — is the clause that "
            "matters for renovating an existing non-complying house."
        ),
    ),
    Extract(
        "part2-classification-of-zones",
        "2",
        "Classification of Zones",
        (16, 19),
        note="Zone list + abbreviations (RR/R1/R2/R3, F, EP, overlays).",
    ),
    Extract(
        "part3-definitions",
        "3",
        "Definitions",
        (20, 43),
        note=(
            "Defined terms, 'Term: definition' per paragraph. Key at retrieval "
            "time: additional residential unit (ARU), dwelling unit, basement, "
            "gross floor area, home occupation, private garage."
        ),
    ),
    Extract(
        "part4-general-provisions",
        "4",
        "General Provisions",
        (44, 67),
        note=(
            "4.19 Additional Residential Units (ARUs) + 4.19.1 attached / "
            "4.19.2 detached provisions — the core basement-apartment zoning "
            "trigger. Also 4.2 barrier-free entrances, 4.8 home occupations."
        ),
    ),
    Extract(
        "part5-parking-loading",
        "5",
        "Parking and Loading Standards",
        (68, 82),
        note=(
            "5.3 parking in residential zones and 5.8 residential parking "
            "requirements govern whether an ARU needs an added parking space."
        ),
    ),
    Extract(
        "part7-residential-zones",
        "7",
        "Residential Zones",
        (84, 89),
        note=(
            "Purpose statements, Table 7.2 permitted uses, Tables 7.3A-7.3E "
            "zone standards (frontage/yards/height/landscaped open space)."
        ),
    ),
    Extract(
        "part11-other-zones",
        "11",
        "Other Zones",
        (93, 93),
        note="Floodway (F) zone — restricts habitable uses.",
    ),
    Extract(
        "part12-environmental-open-space",
        "12",
        "Environmental and Open Space Zones",
        (94, 94),
    ),
    Extract(
        "part13-overlay-zones",
        "13",
        "Overlay Zones",
        (95, 98),
        note=(
            "Floodplain 1/2 overlays — can prohibit or restrict new dwelling "
            "units and below-grade habitable space on affected lots."
        ),
    ),
]

# Running header/footer on every page:
#   "If an accessible format or accommodation is required, please contact"
#   "planning@cambridge.ca" (or accessibility@) and a bare printed page number
HEADER_PATTERNS = [
    re.compile(r"^\s*If an accessible format or accommodation is required.*$"),
    re.compile(r"^\s*(planning|accessibility)@cambridge\.ca\s*$"),
    re.compile(r"^\s*\d{1,3}\s*$"),
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
    a, b = ex.pages
    fields = {
        **COMMON_META,
        "part": ex.part,
        "section_number": ex.part,
        "title": ex.title,
        "pdf_pages": f"{a}-{b}" if a != b else str(a),
    }
    body = "\n".join(f"{k}: {v!r}" for k, v in fields.items())
    return f"---\n{body}\n---\n"


def main() -> None:
    reader = PdfReader(SOURCE_PDF)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for ex in PARTS:
        start, end = ex.pages
        pages = [clean_page(reader.pages[i].extract_text() or "")
                 for i in range(start - 1, end)]
        text = "\n\n".join(pages)
        out = OUT_DIR / f"zb26007-{ex.slug}.md"
        parts = [frontmatter(ex),
                 f"# Cambridge Zoning By-law 26-007 — Part {ex.part}.0: {ex.title}\n"]
        if ex.note:
            parts.append(f"> Extraction note: {ex.note}\n")
        parts.append(text + "\n")
        out.write_text("\n".join(parts))
        print(f"wrote {out.relative_to(REPO)}  ({len(text):,} chars)")


if __name__ == "__main__":
    main()
