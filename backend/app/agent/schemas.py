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
    id: str = ""                         # assigned in code after validation, never by the model
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


# Closed work-category vocabulary -- the union of every material-prices.csv
# and material-allowances-DRAFT-v0.csv `category` column value, plus the
# trade-only structural labels the takeoff assigns to lines that have no
# item/allowance_item of their own (demolition, plumbing, hvac), plus
# "code_required" (the sentinel _enforce_code_coverage injects for a
# mandatory code item the takeoff dropped). Every value here must have a
# row in corpus/guidelines/quote-section-map-DRAFT-v0.csv (see
# app/pricing/quote_sections.py) so the eventual deterministic renderer can
# place every line in its section -- previously `category` was a plain str,
# and price_fill_node/the drafting LLM had no way to reject an
# out-of-vocabulary value the takeoff invented.
TakeoffCategory = Literal[
    "bathroom", "concrete", "doors", "drywall", "electrical", "flooring",
    "framing", "insulation", "kitchen", "paint", "subfloor", "windows",
    "stairs", "demolition", "plumbing", "hvac", "code_required",
]


class TakeoffLine(BaseModel):
    id: str = ""                         # assigned in code after validation
    category: TakeoffCategory            # work-category / price-sheet vocabulary
    item: str = ""                       # material-prices.csv key where possible ("lvp")
    trade: str = ""                      # labor-rates.csv trade key, parallel to item
    allowance_item: str = ""             # material-allowances.csv key for tier-differentiated finishes
    description: str = ""
    quantity: float
    unit: str                            # sqft | linear_ft | each | sheet | gallon | lump_sum
    basis: str = ""                      # auditable: "§4 drywall formula on GFA 900"
    source: Literal["guideline_s4", "comparable", "code_item", "assumption"] = "assumption"
    comparable_ref: str = ""             # project code when source == "comparable"
    code_item_ref: str = ""              # CodeItem.id when source == "code_item"
    instance: str = ""                   # "" for a singleton section; a stable label
                                          # ("bathroom_1", "bathroom_2") when intake
                                          # indicates more than one of this category --
                                          # the eventual renderer repeats that category's
                                          # section heading once per distinct instance


class Takeoff(BaseModel):
    gfa_sqft: float | None = None
    lines: list[TakeoffLine]
    assumptions: list[str] = []


class DraftNarrative(BaseModel):
    """Stage 3's entire LLM contribution to the draft, once render_draft()
    (app/agent/draft_render.py) assembles everything else deterministically
    from already-computed data. project_summary and pricing_confidence are
    the only two things in the whole document that genuinely need judgment
    (§6.2's pricing-confidence policy) rather than arithmetic or a fixed
    template -- structured output here keeps that judgment call from
    reintroducing markdown-formatting variance into the rest of the draft."""
    project_summary: str                 # one short paragraph, plain text (no markdown)
    pricing_confidence: Literal["LOW", "MEDIUM", "HIGH"]
    confidence_reasons: list[str]        # e.g. "no close past-project comparable found"
