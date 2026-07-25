"""Load corpus documents with their frontmatter metadata.

Sources (all pre-processed — see scripts/ and CLAUDE.md data-source notes):
- corpus/OBC/part9_phase1/*.md        (doc_type: building_code)
- corpus/quotes-redacted/*.md         (doc_type: past_project_quote)
- corpus/quotes-synthetic/*.md        (doc_type: past_project_quote, synthetic)
- corpus/guidelines/*.md              (doc_type: builder_guideline)
- corpus/guidelines/*.csv             (doc_type: builder_guideline, tabular)
- corpus/cambridge-zoning-bylaw/parts/*.md  (doc_type: zoning_bylaw; extracted
  from the By-law 26-007 PDF by scripts/extract_zoning_bylaw.py — the
  council-report PDF in the same folder is never extracted or loaded)

Ownership: building_code and zoning_bylaw are SHARED regulatory data — public,
reusable by any contractor, and carry no contractor_id. past_project_quote and
builder_guideline are CONTRACTOR-OWNED and are stamped with the deployment's
contractor_id/contractor_name; retrieval filters on that id, so a second
contractor's docs can coexist in the same collection without cross-talk.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from app.config import CORPUS_DIR
from app.contractor import get_contractor


def _contractor_meta() -> dict:
    p = get_contractor()
    return {"contractor_id": p.id, "contractor_name": p.internal_name}


@dataclass
class CorpusDoc:
    source_file: str  # repo-relative path
    text: str
    metadata: dict = field(default_factory=dict)


def _load_md(path: Path, defaults: dict) -> CorpusDoc:
    post = frontmatter.load(path)
    meta = {**defaults, **{k: v for k, v in post.metadata.items() if v is not None}}
    rel = str(path.relative_to(CORPUS_DIR.parent))
    meta["source_file"] = rel
    return CorpusDoc(source_file=rel, text=post.content, metadata=meta)


def load_obc() -> list[CorpusDoc]:
    docs = []
    for p in sorted((CORPUS_DIR / "OBC/part9_phase1").glob("*.md")):
        docs.append(_load_md(p, {"doc_type": "building_code", "jurisdiction": "ontario"}))
    return docs


def load_quotes() -> list[CorpusDoc]:
    docs = []
    for folder in ("quotes-redacted", "quotes-synthetic"):
        for p in sorted((CORPUS_DIR / folder).glob("*.md")):
            docs.append(_load_md(p, {"doc_type": "past_project_quote",
                                     "jurisdiction": "ontario",
                                     **_contractor_meta()}))
    return docs


def load_guidelines() -> list[CorpusDoc]:
    docs = []
    gdir = CORPUS_DIR / "guidelines"
    for p in sorted(gdir.glob("*.md")):
        docs.append(_load_md(p, {"doc_type": "builder_guideline",
                                 "jurisdiction": "ontario",
                                 **_contractor_meta()}))
    for p in sorted(gdir.glob("*.csv")):
        with open(p, newline="") as f:
            rows = list(csv.reader(f))
        header, body = rows[0], rows[1:]
        text_lines = [", ".join(header)] + [", ".join(r) for r in body]
        rel = str(p.relative_to(CORPUS_DIR.parent))
        docs.append(CorpusDoc(
            source_file=rel,
            text="\n".join(text_lines),
            metadata={
                "doc_type": "builder_guideline",
                "jurisdiction": "ontario",
                **_contractor_meta(),
                "tabular": True,
                "title": p.stem,
                "source_file": rel,
                "csv_header": header,
                "csv_group_column": header[0],
            },
        ))
    return docs


def load_zoning_bylaw() -> list[CorpusDoc]:
    docs = []
    for p in sorted((CORPUS_DIR / "cambridge-zoning-bylaw/parts").glob("*.md")):
        docs.append(_load_md(p, {"doc_type": "zoning_bylaw", "jurisdiction": "cambridge"}))
    return docs


def load_all() -> list[CorpusDoc]:
    return load_obc() + load_quotes() + load_guidelines() + load_zoning_bylaw()
