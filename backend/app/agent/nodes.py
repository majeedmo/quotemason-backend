"""Graph nodes. intake -> (ask | hard_route | codes -> takeoff -> price_fill
-> draft). The three drafting stages produce structured outputs (schemas.py)
persisted with the draft — the substrate of the quote-accuracy eval."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     ToolMessage)
from pydantic import ValidationError

from app.agent import guidelines, prompts, schemas
from app.agent.llm import codes_model, drafting_model, intake_model
from app.agent.state import AgentState
from app.config import settings
from app.pricing import materials
from app.retrieval import get_retriever
from app.tools import regulatory

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)
_JSON_START = re.compile(r'\{\s*"')


def _parse_json_block(raw: str, required_key: str) -> dict | None:
    """Prompted-JSON salvage shared by every stage: accept a clean object,
    else scan for the first embedded object carrying required_key (models
    wrap JSON in prose — seen live 2026-07-14). None when nothing parses."""
    text = _FENCE.sub("", raw.strip())
    try:
        out = json.loads(text)
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in _JSON_START.finditer(text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and required_key in obj:
            return obj
    return None


def _parse_intake(raw: str) -> dict:
    out = _parse_json_block(raw, required_key="action")
    if out is not None:
        return out
    # Model broke format: treat its text as a plain follow-up question —
    # cut before any JSON-ish tail so raw JSON never reaches the client.
    text = _FENCE.sub("", raw.strip())
    m = _JSON_START.search(text)
    reply = (text[: m.start()] if m else text).strip()
    return {"action": "ask",
            "reply": reply or "Could you tell me a bit more about your project?",
            "slots": {}, "flags": [], "hard_trigger": None}


def _validated(model_cls, raw, required_key: str):
    """Salvage-parse then schema-validate; None on any failure (caller
    degrades — a bad stage output must never block drafting)."""
    obj = _parse_json_block(raw if isinstance(raw, str) else json.dumps(raw),
                            required_key)
    if obj is None:
        return None
    try:
        return model_cls.model_validate(obj)
    except ValidationError as e:
        logger.warning("%s validation failed: %s", model_cls.__name__, e)
        return None


def _tag_routing(level: str, categories: list[str]) -> None:
    """§6.3: LangSmith-tag every routing event (best-effort; no-op untraced)."""
    try:
        from langsmith.run_helpers import get_current_run_tree
        rt = get_current_run_tree()
        if rt is not None:
            rt.tags = (rt.tags or []) + [f"route={level}"] + [
                f"trigger={c}" for c in categories]
    except Exception:
        pass


def intake_node(state: AgentState) -> dict:
    last_user = next((m for m in reversed(state["messages"])
                      if m.type == "human"), None)
    det_hits = guidelines.scan_hard_triggers(last_user.content) if last_user else []

    msgs = ([SystemMessage(prompts.intake_system())] + state["messages"]
            + [SystemMessage(f"deterministic_hits: {json.dumps(det_hits)}\n"
                             f"slots_so_far: {json.dumps(state.get('slots', {}))}")])
    out = _parse_intake(intake_model().invoke(msgs).content)

    slots = {**state.get("slots", {}),
             **{k: v for k, v in (out.get("slots") or {}).items() if v is not None}}
    flags = out.get("flags") or state.get("flags", [])
    action = out.get("action", "ask")

    # Precedence guard (§6.3): a deterministic hard hit ends intake even if
    # the model disagreed. Categories come from the doc's lists either way.
    categories = [c for c, _ in det_hits]
    matched = [kw for _, kw in det_hits]
    if out.get("hard_trigger"):
        categories.append(out["hard_trigger"].get("category", "model-judgment"))
        matched.append(out["hard_trigger"].get("evidence", ""))
    if det_hits:
        action = "hard_route"

    level = "hard" if action == "hard_route" else ("flag" if flags else "clear")
    if level != "clear":
        _tag_routing(level, categories)

    return {"messages": [AIMessage(out.get("reply", ""))],
            "slots": slots, "flags": flags,
            "trigger": {"level": level, "categories": categories,
                        "matched": matched},
            "_action": action}


def hard_route_node(state: AgentState) -> dict:
    """§6.3 routing packet for the estimator; the client ack was already
    composed by the intake model (reply in messages)."""
    transcript = [f"{m.type}: {m.content}" for m in state["messages"]]
    return {"routing_packet": {
        "route": "hard",
        "triggers": state["trigger"].get("categories", []),
        "matched_text": state["trigger"].get("matched", []),
        "slots": state.get("slots", {}),
        "transcript": transcript,
    }}


def _pack(hits):
    """Dedup retrieval hits by citation into draft-context rows."""
    seen, out = set(), []
    for h in hits:
        if h.citation in seen:
            continue
        seen.add(h.citation)
        out.append({"citation": h.citation, "text": h.text})
    return out


def _tier(slots: dict) -> str | None:
    tb = str(slots.get("package_tier_budget", "") or "")
    for t in ("ESSENTIAL", "SUPERIOR", "SUPREME"):
        if t in tb.upper():
            return t
    return None


def _checklist_from_seeds(seeds: dict) -> schemas.CodesChecklist:
    """Deterministic degrade path: a checklist built straight from the
    applicable_codes() seed rows, so the draft always has cited code context
    even when the codes model output can't be parsed."""
    items = [schemas.CodeItem(
        requirement=row["citation"], citation=row["citation"], doc_type=dt,
        section_number=row.get("section_number", ""),
        applies_because="deterministic baseline applicability check",
        action="verify_on_site")
        for dt, rows in seeds.items() for row in rows]
    return schemas.CodesChecklist(
        zoning_jurisdiction=settings.zoning_jurisdiction, items=items,
        notes="deterministic fallback — codes model output could not be parsed")


_RETRY_JSON = "Respond now with ONLY the JSON object in the required schema."


def codes_node(state: AgentState) -> dict:
    """Stage 1: applicable-codes checklist. Seeded by the deterministic
    regulatory baseline; the model may add project-specific lookups via
    tool-calling (bounded), then emits a schema-validated checklist."""
    s = state.get("slots", {})
    seeds = regulatory.applicable_codes(s)
    tool_rows: list[dict] = []
    checklist = None
    try:
        tools = {t.name: t for t in regulatory.REGULATORY_TOOLS}
        bound = codes_model().bind_tools(regulatory.REGULATORY_TOOLS)
        msgs = [SystemMessage(prompts.codes_system()),
                HumanMessage(prompts.codes_user(
                    s, seeds, state.get("estimator_feedback")))]
        resp = bound.invoke(msgs)
        for _ in range(3):
            calls = getattr(resp, "tool_calls", None) or []
            if not calls:
                break
            msgs.append(resp)
            for tc in calls:
                tool = tools.get(tc.get("name"))
                try:
                    result = tool.invoke(tc.get("args") or {}) if tool else []
                except Exception as e:
                    result = [{"error": str(e)}]
                tool_rows.extend(r for r in result if isinstance(r, dict)
                                 and "citation" in r)
                msgs.append(ToolMessage(json.dumps(result, default=str),
                                        tool_call_id=tc.get("id", "")))
            resp = bound.invoke(msgs)
        if getattr(resp, "tool_calls", None):
            # still asking after the cap — force a final, tool-free answer
            resp = codes_model().invoke(msgs + [HumanMessage(_RETRY_JSON)])
        checklist = _validated(schemas.CodesChecklist, resp.content, "items")
        if checklist is None:
            retry = codes_model().invoke(
                msgs + [resp, HumanMessage(_RETRY_JSON)])
            checklist = _validated(schemas.CodesChecklist, retry.content, "items")
    except Exception:
        logger.exception("codes stage failed — using deterministic checklist")
    if checklist is None:
        checklist = _checklist_from_seeds(seeds)

    def rows_for(dt: str) -> list[dict]:
        rows = list(seeds.get(dt, [])) + [r for r in tool_rows
                                          if r.get("doc_type") == dt]
        seen, out = set(), []
        for r in rows:
            if r["citation"] in seen:
                continue
            seen.add(r["citation"])
            out.append({"citation": r["citation"], "text": r.get("text", "")})
        return out

    retrieved = {**state.get("retrieved", {}),
                 "building_code": rows_for("building_code")}
    zoning = rows_for("zoning_bylaw")
    if zoning:
        retrieved["zoning_bylaw"] = zoning
    return {"codes_checklist": checklist.model_dump(), "retrieved": retrieved}


def takeoff_node(state: AgentState) -> dict:
    """Stage 2: structured material-quantity takeoff from §4 rules of thumb,
    the codes checklist, and comparable past projects. Degrades to None —
    stage 3 then drafts from raw context, as the single-shot drafter did."""
    s = state.get("slots", {})
    scope = str(s.get("scope", "basement"))
    tier = _tier(s)
    quote_q = (f"{scope} {s.get('gfa_sqft', '')} sqft "
               f"{'separate entrance' if s.get('separate_entrance') else ''} "
               f"{s.get('kitchen', '')} {tier or ''}")
    comparables = _pack(get_retriever().search_past_quotes(
        quote_q, k=6, package_tier=tier))
    item_keys = sorted(f"{c}/{i}" for c, i in materials.load_price_sheet())
    takeoff = None
    try:
        msgs = [SystemMessage(prompts.takeoff_system()),
                HumanMessage(prompts.takeoff_user(
                    s, guidelines.section("4"), comparables,
                    state.get("codes_checklist") or {}, item_keys,
                    state.get("estimator_feedback")))]
        m = drafting_model()
        resp = m.invoke(msgs)
        takeoff = _validated(schemas.Takeoff, resp.content, "lines")
        if takeoff is None:
            retry = m.invoke(msgs + [resp, HumanMessage(_RETRY_JSON)])
            takeoff = _validated(schemas.Takeoff, retry.content, "lines")
    except Exception:
        logger.exception("takeoff stage failed — drafting from raw context")
    return {"takeoff": takeoff.model_dump() if takeoff else None,
            "retrieved": {**state.get("retrieved", {}),
                          "past_project_quote": comparables}}


def price_fill_node(state: AgentState) -> dict:
    """Deterministic price resolution — no LLM. Sheet-first (staleness-gated),
    per-item web fallback when a Tavily key is present, honest 'unpriced'
    rows otherwise. Arithmetic happens here in code so the accuracy eval can
    assert it."""
    lines = (state.get("takeoff") or {}).get("lines") or []
    rows: list[dict] = []
    tavily_client = None
    tavily_used = 0
    for line in lines:
        cat, item = str(line.get("category", "")), str(line.get("item", ""))
        qty = float(line.get("quantity") or 0)
        base = {"category": cat, "item": item,
                "description": line.get("description", ""),
                "quantity": qty, "unit": line.get("unit", "")}
        sheet_row = materials.lookup(cat, item) if cat and item else None
        if sheet_row and not materials.is_stale(sheet_row):
            rows.append({**base,
                         "unit_price_low_cad": sheet_row.price_low_cad,
                         "unit_price_high_cad": sheet_row.price_high_cad,
                         "extended_low_cad": round(qty * sheet_row.price_low_cad, 2),
                         "extended_high_cad": round(qty * sheet_row.price_high_cad, 2),
                         "sheet_unit": sheet_row.unit,
                         "price_source": "price_sheet",
                         "source_detail": (f"{sheet_row.source} (updated "
                                           f"{sheet_row.updated_at.isoformat()})"),
                         "stale": False})
            continue
        status = "stale" if sheet_row else "missing"
        if settings.tavily_api_key and tavily_used < 3:
            try:
                if tavily_client is None:
                    from tavily import TavilyClient
                    tavily_client = TavilyClient(api_key=settings.tavily_api_key)
                q = f"{line.get('description') or item} price Ontario"
                resp = tavily_client.search(q, max_results=3, include_answer=True)
                tavily_used += 1
                rows.append({**base, "price_source": "tavily",
                             "sheet_status": status, "query": q,
                             "answer": resp.get("answer"),
                             "results": [{"title": x["title"], "url": x["url"]}
                                         for x in resp.get("results", [])]})
            except Exception as e:
                rows.append({**base, "price_source": "unpriced",
                             "sheet_status": status,
                             "note": f"web price check failed ({e}) — "
                                     "estimator to price"})
            continue
        rows.append({**base, "price_source": "unpriced", "sheet_status": status,
                     "note": (f"no fresh sheet price ({status})"
                              + ("" if settings.tavily_api_key
                                 else " and TAVILY_API_KEY not set")
                              + " — estimator to price")})
    return {"price_resolution": rows}


def draft_node(state: AgentState) -> dict:
    """Stage 3: the cited quote draft, from the structured stage outputs plus
    the contractor's guideline context (retrieved here, where it's used)."""
    s = state.get("slots", {})
    retrieved = {**state.get("retrieved", {}),
                 "builder_guideline": _pack(get_retriever().search_guidelines(
                     f"allowances rules of thumb {_tier(s) or ''} "
                     f"{s.get('scope', 'basement')}", k=4))}
    msgs = [SystemMessage(prompts.draft_system()),
            ("user", prompts.draft_user(s,
                                        state.get("flags", []),
                                        retrieved,
                                        state.get("codes_checklist"),
                                        state.get("takeoff"),
                                        state.get("price_resolution", [])))]
    feedback = state.get("estimator_feedback")
    if feedback:
        msgs.append(("user",
                     "REVISION REQUEST from the reviewing estimator (not the "
                     "client). Previous draft:\n\n"
                     f"{state.get('draft') or '(none)'}\n\n"
                     f"Requested changes:\n{feedback}\n\n"
                     "Produce the complete revised quote, keeping the same "
                     "citation discipline."))
    draft = drafting_model().invoke(msgs).content
    packet = None
    if state.get("flags"):
        packet = {"route": "flag",
                  "triggers": [f["condition"] for f in state["flags"]],
                  "slots": state.get("slots", {})}
    return {"draft": draft, "routing_packet": packet,
            "estimator_feedback": None,
            "retrieved": retrieved,
            "messages": [AIMessage("Draft quote prepared — routed to the "
                                   "estimator for review before anything "
                                   "reaches you. (Draft attached below.)\n\n"
                                   + draft)]}
