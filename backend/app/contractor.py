"""The contractor this deployment serves — the single place identity and
contractor-owned corpus paths are derived from settings.

Deliberately NOT a registry: one process serves one contractor. Everything
that used to hardcode "Company A" (chunk prefixes, citations, prompts, the
guideline doc path) resolves through here instead, so onboarding another
contractor is config + data, not code.

Corpus ownership map:
- shared regulatory (no contractor_id): corpus/OBC/, corpus/cambridge-zoning-bylaw/
- contractor-owned (stamped with contractor_id): corpus/guidelines/,
  corpus/quotes-redacted/, corpus/quotes-synthetic/ (company-a's, grandfathered
  at the top level) and corpus/contractors/<id>/ (where new contractor files
  live, e.g. the material price sheet).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import CORPUS_DIR, settings


@dataclass(frozen=True)
class ContractorProfile:
    id: str
    internal_name: str      # appears in guideline text/citations ("Company A")
    brand_name: str         # what the client sees ("Maplewood Renovations")
    guideline_doc: Path
    prices_csv: Path


@lru_cache(maxsize=1)
def get_contractor() -> ContractorProfile:
    contractor_dir = CORPUS_DIR / "contractors" / settings.contractor_id
    return ContractorProfile(
        id=settings.contractor_id,
        internal_name=settings.contractor_name,
        brand_name=settings.brand_name,
        guideline_doc=CORPUS_DIR / "guidelines" / "builder-guidelines-DRAFT-v0.md",
        prices_csv=contractor_dir / "material-prices.csv",
    )
