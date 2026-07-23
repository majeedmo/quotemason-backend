"""Structure-aware chunking (CLAUDE.md: by section/clause, never fixed token size).

Each chunk:
- carries full metadata (jurisdiction, doc_type, section_number, title,
  effective_date/source_version, source_file, …),
- has its section number + title PREFIXED into the text itself, so an isolated
  retrieved chunk still carries citation lineage,
- gets a deterministic ID (sha256 of source + section + position) so re-running
  ingestion upserts instead of duplicating.

Strategies by doc_type:
- building_code       -> split at OBC article/subsection headings (9.5.3.1. Title);
                         Division A defined terms -> batches of whole definitions
- past_project_quote  -> split at numbered work-category headings + boilerplate blocks
- builder_guideline   -> markdown: split at ## / ### headings; CSV: group rows by
                         first column (trade/category), header repeated per chunk
- zoning_bylaw        -> split at bylaw section headings (4.19 Additional
                         Residential Units (ARUs)); Part 3 definitions -> batches
                         of whole 'Term: definition' entries
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.ingestion.loaders import CorpusDoc

MAX_CHUNK_CHARS = 4000  # size guard only; structure decides the real boundaries
MIN_CHUNK_CHARS = 80


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)


def _make_id(source_file: str, section: str, idx: int) -> str:
    return hashlib.sha256(f"{source_file}|{section}|{idx}".encode()).hexdigest()[:32]


def _batch(pieces: list[str], sep: str) -> list[str]:
    parts, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) > MAX_CHUNK_CHARS:
            parts.append(buf.strip())
            buf = ""
        buf += p + sep
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _size_guard(text: str) -> list[str]:
    """Split an oversized section at paragraph boundaries; fall back to single
    lines for extraction formats (docx tables) that have no blank lines."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts = _batch(text.split("\n\n"), "\n\n")
    out: list[str] = []
    for p in parts:
        out.extend(_batch(p.split("\n"), "\n") if len(p) > MAX_CHUNK_CHARS else [p])
    return out


def _emit(doc: CorpusDoc, sections: list[tuple[str, str, str]], prefix_fmt: str) -> list[Chunk]:
    """sections: (section_number, section_title, body). Applies size guard + prefixes."""
    chunks: list[Chunk] = []
    for i, (num, title, body) in enumerate(sections):
        body = body.strip()
        if len(body) < MIN_CHUNK_CHARS:
            continue
        for j, piece in enumerate(_size_guard(body)):
            meta = {**doc.metadata, "section_number": num, "section_title": title}
            prefix = prefix_fmt.format(num=num, title=title, **{
                k: doc.metadata.get(k, "") for k in
                ("project_code", "city", "package_tier", "scope", "source_version")})
            cont = f" (cont. {j + 1})" if j else ""
            chunks.append(Chunk(
                id=_make_id(doc.source_file, num or title, i * 100 + j),
                text=f"{prefix}{cont}\n{piece}",
                metadata=meta,
            ))
    return chunks


# --- building code -----------------------------------------------------------

# OBC article headings as they survive PDF extraction, e.g.
#   "9.5.3.1. Ceiling Heights of Rooms or Spaces"  /  "9.5.3A.1. ..." / "9.9.9. Egress from Dwelling Units"
_OBC_HEADING = re.compile(
    r"^\s*(\d{1,2}\.\d{1,2}(?:\.\d{1,2}[A-Z]?){1,2}\.?)\s+([A-Z][^\n]{3,90})$", re.M)
# Division A defined terms: "Secondary suite means a self-contained ..."
_DEFINED_TERM = re.compile(r"^([A-Z][A-Za-z\-’' ]{2,60})\s+means\b", re.M)


def chunk_building_code(doc: CorpusDoc) -> list[Chunk]:
    ver = doc.metadata.get("source_version", "")
    prefix = "[OBC {num} — {title} | {source_version}]".replace("{source_version}", ver)
    if str(doc.metadata.get("section_number", "")).startswith("1.4"):
        return _chunk_defined_terms(doc)
    heads = list(_OBC_HEADING.finditer(doc.text))
    if not heads:
        return _emit(doc, [(str(doc.metadata.get("section_number", "")),
                            str(doc.metadata.get("title", "")), doc.text)], prefix)
    sections: list[tuple[str, str, str]] = []
    # preamble before the first article keeps the file-level section number;
    # skip it when it is only page-header residue from the PDF extraction
    pre = doc.text[: heads[0].start()].strip()
    if pre and not (len(pre) < 400 and "Building Code Compendium" in pre):
        sections.append((str(doc.metadata.get("section_number", "")),
                         str(doc.metadata.get("title", "")), pre))
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(doc.text)
        num = h.group(1).rstrip(".")
        title = h.group(2).strip().rstrip(". ")
        sections.append((num, title, doc.text[h.start():end]))
    return _emit(doc, sections, prefix)


def _chunk_defined_terms(doc: CorpusDoc, batch_chars: int = 1800) -> list[Chunk]:
    """Division A 1.4.1.2: batch whole term definitions, never split inside one."""
    ver = doc.metadata.get("source_version", "")
    starts = [m.start() for m in _DEFINED_TERM.finditer(doc.text)]
    if not starts:
        return _emit(doc, [("1.4.1.2", "Defined Terms", doc.text)],
                     f"[OBC {{num}} — {{title}} | {ver}]")
    defs = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(doc.text)
        defs.append(doc.text[s:e].strip())
    sections, buf, first_term = [], "", ""
    for d in defs:
        term = _DEFINED_TERM.match(d).group(1).strip()
        if buf and len(buf) + len(d) > batch_chars:
            sections.append(("1.4.1.2", f"Defined Terms ({first_term} …)", buf))
            buf, first_term = "", ""
        if not buf:
            first_term = term
        buf += d + "\n\n"
    if buf.strip():
        sections.append(("1.4.1.2", f"Defined Terms ({first_term} …)", buf))
    return _emit(doc, sections, f"[OBC {{num}} — {{title}} | {ver}]")


# --- past project quotes ------------------------------------------------------

# numbered work-category headings, e.g. "3 ONE FULL KITCHEN: (Supply & Install)"
# or table rows like "1 | SEPARATE ENTRANCE / THREE NEW WINDOWS: |"
_QUOTE_HEADING = re.compile(
    r"^\s*(\d{1,2})\s*\|?\s+([A-Z][A-Z0-9 /&()+.,'’-]{4,80}?):", re.M)
_QUOTE_BOILERPLATE = re.compile(
    # AGREEMENT OF SERVICES / CHANGE ORDER POLICY / WARRANTY are stripped
    # entirely at redaction time (scripts/redact_quotes.py strip_boilerplate)
    # -- verbatim-identical contractual boilerplate across every quote,
    # already the guideline doc's job (§5) -- so they can no longer appear
    # here; CHANGE ORDERS kept as a safety net (an inline, non-heading
    # variant of that text isn't caught by the redaction-time stripper).
    r"^\s*(CHANGE ORDERS|EXCLUSIONS|"
    r"OUT OF SCOPE|ADD[- ]?ONS?|PROJECT COST|RENOVATION / CONSTRUCTION MILESTONES|NOTE)\b[:\s]*$",
    re.M | re.I)


def chunk_quote(doc: CorpusDoc) -> list[Chunk]:
    code = doc.metadata.get("project_code", "?")
    syn = " (SYNTHETIC)" if doc.metadata.get("synthetic") else ""
    prefix = (f"[Past project {code}{syn} — {{city}}, {{package_tier}} package, {{scope}}"
              " | section: {num} {title}]")
    marks = sorted(
        [(m.start(), m.group(1) or "", m.group(2).strip()) for m in _QUOTE_HEADING.finditer(doc.text)]
        + [(m.start(), "", m.group(1).strip().upper()) for m in _QUOTE_BOILERPLATE.finditer(doc.text)])
    if not marks:
        return _emit(doc, [("", "full quote", doc.text)], prefix)
    sections: list[tuple[str, str, str]] = []
    pre = doc.text[: marks[0][0]].strip()
    if pre:
        sections.append(("", "header & terms", pre))
    for k, (pos, num, title) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(doc.text)
        sections.append((num, title.title(), doc.text[pos:end]))
    return _emit(doc, sections, prefix)


# --- guidelines ---------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{2,3})\s+(.+)$", re.M)


def chunk_guideline_md(doc: CorpusDoc) -> list[Chunk]:
    contractor = doc.metadata.get("contractor_name", "Builder")
    prefix = f"[{contractor} builder guidelines ({{source_version}}) — {{num}} {{title}}]"
    heads = list(_MD_HEADING.finditer(doc.text))
    if not heads:
        return _emit(doc, [("", str(doc.metadata.get("title", "")), doc.text)], prefix)
    sections: list[tuple[str, str, str]] = []
    pre = doc.text[: heads[0].start()].strip()
    if pre:
        sections.append(("", "preamble", pre))
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(doc.text)
        title = h.group(2).strip()
        m = re.match(r"^(\d+[\d.]*)\.?\s+(.*)", title)
        num, t = (m.group(1), m.group(2)) if m else ("", title)
        sections.append((num, t, doc.text[h.start():end]))
    return _emit(doc, sections, prefix)


def chunk_guideline_csv(doc: CorpusDoc) -> list[Chunk]:
    lines = doc.text.split("\n")
    header, rows = lines[0], lines[1:]
    groups: dict[str, list[str]] = {}
    for r in rows:
        if not r.strip():
            continue
        key = r.split(",", 1)[0]
        groups.setdefault(key, []).append(r)
    sections = [(key, f"{doc.metadata.get('title', '')} — {key}",
                 header + "\n" + "\n".join(rs)) for key, rs in groups.items()]
    contractor = doc.metadata.get("contractor_name", "Builder")
    return _emit(doc, sections, f"[{contractor} rate/allowance table {{title}}]")


# --- zoning bylaw ---------------------------------------------------------------

# By-law 26-007 section headings as extracted, e.g.
#   "4.19 Additional Residential Units (ARUs)" / "7.1.1 Rural Residential (RR) Zone"
# (dotted number required, so quote-style table rows "1 Minimum required..." don't match;
# long titles wrap in the PDF, so a captured title may be its first line only)
_ZB_HEADING = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){1,2})\s+([A-Z][^\n]{3,90})$", re.M)
# Part 3 defined terms: "Additional residential unit (ARU): a self-contained ..."
_ZB_TERM = re.compile(r"^([A-Z][A-Za-z0-9\-\u2013\u2019'(),/ ]{2,70}?):\s", re.M)

def chunk_zoning_bylaw(doc: CorpusDoc) -> list[Chunk]:
    ver = doc.metadata.get("source_version", "")
    prefix = ("[Cambridge Zoning By-law 26-007 \u00a7{num} \u2014 {title} | "
              + ver + "]")
    if str(doc.metadata.get("part", "")) == "3":
        return _chunk_zb_definitions(doc, prefix)
    heads = list(_ZB_HEADING.finditer(doc.text))
    if not heads:
        return _emit(doc, [(str(doc.metadata.get("section_number", "")),
                            str(doc.metadata.get("title", "")), doc.text)], prefix)
    sections: list[tuple[str, str, str]] = []
    pre = doc.text[: heads[0].start()].strip()
    if pre:
        sections.append((str(doc.metadata.get("section_number", "")),
                         str(doc.metadata.get("title", "")), pre))
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(doc.text)
        sections.append((h.group(1), h.group(2).strip().rstrip(". "),
                         doc.text[h.start():end]))
    return _emit(doc, sections, prefix)


def _chunk_zb_definitions(doc: CorpusDoc, prefix: str, batch_chars: int = 1800) -> list[Chunk]:
    """Part 3.0: batch whole 'Term: definition' entries, never split inside one."""
    starts = [m.start() for m in _ZB_TERM.finditer(doc.text)]
    if not starts:
        return _emit(doc, [("3", "Definitions", doc.text)], prefix)
    defs = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(doc.text)
        defs.append(doc.text[s:e].strip())
    sections, buf, first_term = [], "", ""
    for d in defs:
        term = _ZB_TERM.match(d).group(1).strip()
        if buf and len(buf) + len(d) > batch_chars:
            sections.append(("3", f"Definitions ({first_term} \u2026)", buf))
            buf, first_term = "", ""
        if not buf:
            first_term = term
        buf += d + "\n\n"
    if buf.strip():
        sections.append(("3", f"Definitions ({first_term} \u2026)", buf))
    return _emit(doc, sections, prefix)


# --- dispatch -----------------------------------------------------------------

def chunk_doc(doc: CorpusDoc) -> list[Chunk]:
    dt = doc.metadata.get("doc_type")
    if dt == "building_code":
        return chunk_building_code(doc)
    if dt == "past_project_quote":
        return chunk_quote(doc)
    if dt == "builder_guideline":
        return chunk_guideline_csv(doc) if doc.metadata.get("tabular") else chunk_guideline_md(doc)
    if dt == "zoning_bylaw":
        return chunk_zoning_bylaw(doc)
    raise ValueError(f"no chunking strategy for doc_type={dt!r} ({doc.source_file})")
