"""Structured stage outputs for the 3-stage drafting pipeline.

These are the pipeline's checkable substrate: the codes checklist (stage 1)
and material takeoff (stage 2) are LLM-produced but schema-validated; the
price resolution (between stages 2 and 3) is built in code. All three are
persisted with the draft (quote_drafts.stage_outputs) so the quote-accuracy
eval can assert against structure — quantities, citations, price sources —
instead of judging prose.

Parsing contract: models are prompted for bare JSON (same contract as
intake), salvaged with nodes._parse_json_block, then validated here. On
validation failure the pipeline degrades (codes -> deterministic seed
checklist; takeoff -> None) — the draft is never blocked.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CodeItem(BaseModel):
    requirement: str                     # what must be done or verified
    citation: str                        # verbatim citation string from a retrieved row
    doc_type: Literal["building_code", "zoning_bylaw"]
    section_number: str = ""
    applies_because: str = ""            # traces to a slot/spec ("bedroom with no egress window")
    action: Literal["line_item", "verify_on_site", "informational"] = "informational"


class CodesChecklist(BaseModel):
    zoning_jurisdiction: str = ""
    items: list[CodeItem]
    notes: str = ""


class TakeoffLine(BaseModel):
    category: str                        # work-category / price-sheet vocabulary
    item: str                            # price-sheet/allowances key where possible ("lvp")
    description: str = ""
    quantity: float
    unit: str                            # sqft | linear_ft | each | sheet | gallon | lump_sum
    basis: str = ""                      # auditable: "§4 drywall formula on GFA 900"
    source: Literal["guideline_s4", "comparable", "code_item", "assumption"] = "assumption"
    comparable_ref: str = ""             # project code when source == "comparable"


class Takeoff(BaseModel):
    gfa_sqft: float | None = None
    lines: list[TakeoffLine]
    assumptions: list[str] = []
