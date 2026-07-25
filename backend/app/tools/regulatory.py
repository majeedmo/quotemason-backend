"""Shared regulatory lookup — OBC Part 9 + municipal zoning as a service.

Building code and zoning are PUBLIC data, not any contractor's corpus: their
chunks carry no contractor_id, and this module is the boundary every
contractor deployment (and the future MCP server) calls instead of issuing
raw retrieval queries. MCP-shaped on purpose: stateless functions with
JSON-serializable inputs/outputs, so an MCP wrapper is a 1:1 decoration —
no logic moves.

Two layers:
- search_building_code / search_zoning_provisions — adaptive lookups the
  drafting agent can drive via tool-calling (REGULATORY_TOOLS);
- applicable_codes(project_specs) — the deterministic baseline: the known
  always-check queries (ceiling height; egress when bedrooms are planned;
  fire separation / alarms / change-of-use / zoning when the project is an
  accessory unit). Seeds the codes stage so a run with zero tool calls
  still covers the floor.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.config import settings
from app.retrieval.retriever import get_retriever


def _pack(hits) -> list[dict]:
    """Dedup by citation; JSON-serializable rows a checklist can cite."""
    seen, out = set(), []
    for h in hits:
        if h.citation in seen:
            continue
        seen.add(h.citation)
        out.append({"citation": h.citation, "text": h.text,
                    "section_number": h.metadata.get("section_number", ""),
                    "doc_type": h.metadata.get("doc_type", "")})
    return out


def search_building_code(query: str, k: int = 3) -> list[dict]:
    """Semantic + lexical search over OBC Part 9 (jurisdiction: ontario)."""
    return _pack(get_retriever().search_building_code(query, k=k))


def search_zoning_provisions(query: str, k: int = 3,
                             jurisdiction: str | None = None) -> list[dict]:
    """Search the municipal zoning by-law; None means the deployment's
    configured jurisdiction (settings.zoning_jurisdiction)."""
    return _pack(get_retriever().search_zoning(
        query, k=k, jurisdiction=jurisdiction or settings.zoning_jurisdiction))


def applicable_codes(project_specs: dict,
                     jurisdiction: str | None = None) -> dict:
    """Deterministic baseline code/zoning context for a project.

    project_specs uses the intake-slot vocabulary (scope, bedrooms_egress,
    …). Returns {"building_code": [rows], "zoning_bylaw": [rows]} — the
    zoning key present only when the scope is an accessory unit.
    """
    specs = project_specs or {}
    scope = str(specs.get("scope", "basement"))
    accessory = "accessory" in scope.lower()

    queries = ["minimum ceiling height basement rooms"]
    if specs.get("bedrooms_egress"):
        queries.append("bedroom egress window requirements")
    if accessory:
        queries += ["fire separation between dwelling units",
                    "smoke alarms carbon monoxide alarms secondary suite",
                    "change of use second suite requirements"]

    r = get_retriever()
    out = {"building_code": _pack(
        [h for q in queries for h in r.search_building_code(q, k=3)])}
    if accessory:
        out["zoning_bylaw"] = search_zoning_provisions(
            "additional residential unit basement apartment requirements parking",
            k=3, jurisdiction=jurisdiction)
    return out


# --- LLM tool-calling surface (bound by the codes drafting stage) -------------

@tool
def building_code_lookup(query: str) -> list[dict]:
    """Search the Ontario Building Code Part 9 for requirements relevant to a
    residential renovation. Use focused queries naming the requirement or
    clause ("basement window natural light area", "9.9.10 egress window",
    "stair rise run dimensions"). Returns citation + text rows."""
    return search_building_code(query)


@tool
def zoning_lookup(query: str) -> list[dict]:
    """Search the municipal zoning by-law of the project's jurisdiction
    (parking, setbacks, additional-residential-unit permissions, definitions).
    Returns citation + text rows."""
    return search_zoning_provisions(query)


REGULATORY_TOOLS = [building_code_lookup, zoning_lookup]
