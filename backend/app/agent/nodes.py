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

from app.agent import draft_render, guidelines, prompts, schemas
from app.agent.llm import (codes_model, drafting_model, intake_model,
                          takeoff_model, takeoff_verifier_model)
from app.agent.state import AgentState
from app.config import settings
from app.pricing import allowances, labor, materials
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


def _usage_entry(stage: str, resp) -> dict:
    """Per-call LLM usage/cost, read live off the fresh response object right
    after .invoke() -- OpenRouter's own billed cost (response_metadata's
    token_usage.cost) is reliable read this way; it's only re-reading it
    LATER from a LangSmith trace that's documented as flaky. Degrades to
    zeros/None for test doubles lacking these attributes (a bare
    SimpleNamespace(content=...) fake), never raises."""
    usage = getattr(resp, "usage_metadata", None) or {}
    meta = getattr(resp, "response_metadata", None) or {}
    token_usage = meta.get("token_usage", {}) or {}
    return {"stage": stage, "model": meta.get("model_name", ""),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cost_usd": token_usage.get("cost")}


def _generation_summary(stats: list[dict]) -> dict:
    """Cumulative cost/usage for a quote's own detail view -- deterministic,
    computed in code from real per-call figures, never estimated. Same
    philosophy as draft_render.total_contract_value: the pipeline computes
    the number, the LLM/UI just displays it."""
    return {
        "total_cost_usd": round(sum(s["cost_usd"] for s in stats
                                    if s.get("cost_usd") is not None), 4),
        "cost_is_complete": all(s.get("cost_usd") is not None for s in stats),
        "total_input_tokens": sum(s.get("input_tokens", 0) for s in stats),
        "total_output_tokens": sum(s.get("output_tokens", 0) for s in stats),
        "llm_calls": len(stats),
    }


def intake_node(state: AgentState) -> dict:
    last_user = next((m for m in reversed(state["messages"])
                      if m.type == "human"), None)
    det_hits = guidelines.scan_hard_triggers(last_user.content) if last_user else []

    msgs = ([SystemMessage(prompts.intake_system())] + state["messages"]
            + [SystemMessage(f"deterministic_hits: {json.dumps(det_hits)}\n"
                             f"slots_so_far: {json.dumps(state.get('slots', {}))}")])
    resp = intake_model().invoke(msgs)
    out = _parse_intake(resp.content)

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
            "_action": action,
            "generation_stats": [_usage_entry("intake", resp)]}


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
    stats: list[dict] = []
    try:
        tools = {t.name: t for t in regulatory.REGULATORY_TOOLS}
        bound = codes_model().bind_tools(regulatory.REGULATORY_TOOLS)
        msgs = [SystemMessage(prompts.codes_system()),
                HumanMessage(prompts.codes_user(
                    s, seeds, state.get("estimator_feedback")))]
        resp = bound.invoke(msgs)
        stats.append(_usage_entry("codes", resp))
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
            stats.append(_usage_entry("codes", resp))
        if getattr(resp, "tool_calls", None):
            # still asking after the cap — force a final, tool-free answer
            resp = codes_model().invoke(msgs + [HumanMessage(_RETRY_JSON)])
            stats.append(_usage_entry("codes", resp))
        checklist = _validated(schemas.CodesChecklist, resp.content, "items")
        if checklist is None:
            retry = codes_model().invoke(
                msgs + [resp, HumanMessage(_RETRY_JSON)])
            stats.append(_usage_entry("codes", retry))
            checklist = _validated(schemas.CodesChecklist, retry.content, "items")
    except Exception:
        logger.exception("codes stage failed — using deterministic checklist")
    if checklist is None:
        checklist = _checklist_from_seeds(seeds)
    for i, item in enumerate(checklist.items, start=1):
        item.id = f"c{i}"  # assigned in code, never by the model — no drift/collision

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
    return {"codes_checklist": checklist.model_dump(), "retrieved": retrieved,
            # codes_node always precedes the FIRST takeoff attempt of a
            # top-level pipeline invocation (fresh intake or a /revise); the
            # verify_takeoff retry loop itself goes takeoff -> verify_takeoff
            # -> takeoff directly without passing back through here -- so
            # this reset fires exactly once per invocation. Without it, a
            # /revise on an already-flagged thread could inherit a stale
            # attempt count from the checkpointer and skip the retry it
            # should get.
            "takeoff_issues": [], "takeoff_verify_attempts": 0,
            "generation_stats": stats}


def _enforce_code_coverage(takeoff: schemas.Takeoff,
                           checklist: schemas.CodesChecklist) -> None:
    """Every mandatory ("line_item") code requirement must have a traceable
    takeoff line. The takeoff prompt instructs this but doesn't enforce it —
    a dropped requirement would otherwise vanish from the draft silently.
    Mutates takeoff.lines in place, injecting an unpriced placeholder line
    for anything missing (never drops it, mirrors price_fill's philosophy)."""
    mandatory = {i.id for i in checklist.items if i.action == "line_item"}
    covered = {ln.code_item_ref for ln in takeoff.lines if ln.code_item_ref}
    by_id = {i.id: i for i in checklist.items}
    next_idx = len(takeoff.lines) + 1
    for missing_id in sorted(mandatory - covered):
        item = by_id[missing_id]
        takeoff.lines.append(schemas.TakeoffLine(
            id=f"t{next_idx}", category="code_required", quantity=0,
            unit="lump_sum", description=item.requirement,
            basis=f"mandatory per codes checklist ({item.citation}) — "
                  "takeoff stage did not quantify this",
            source="code_item", code_item_ref=missing_id))
        next_idx += 1


def _drop_non_line_item_code_refs(takeoff: schemas.Takeoff,
                                  checklist: schemas.CodesChecklist) -> None:
    """A "verify_on_site"/"informational" checklist item is an attention
    check, not billable or quotable work -- it has no item/trade/allowance
    of its own, so a takeoff line for one always falls into price_fill's
    generic "no material or labor key on this line" bucket, indistinguishable
    downstream from a genuine missing price. Confirmed live: this put lines
    like "Verify ceiling height meets OBC 9.5.3.1" in the estimator's "needs
    a price" queue, asking for a dollar figure on something that was never a
    cost. The takeoff prompt only instructs creating a line for "line_item"
    actions but doesn't forbid the others, and the model does so
    inconsistently (2 of 4 identical-spec quotes did this, 2 didn't) -- drop
    any line tied to a non-"line_item" checklist entry here rather than rely
    on prompt compliance. These items still reach the estimator, just via
    draft_render's dedicated on-site-verifications section, not a price
    line."""
    by_id = {i.id: i for i in checklist.items}
    takeoff.lines = [
        ln for ln in takeoff.lines
        if not (ln.code_item_ref and by_id.get(ln.code_item_ref)
               and by_id[ln.code_item_ref].action != "line_item")
    ]


# trade -> (category, unit) its baseline placeholder line is grouped under
# if the takeoff drops it entirely (app/pricing/quote_sections.py already
# maps every category here to a real section). Lump-sum trades need no
# quantity estimate at all (labor.py ignores quantity for a lump-sum rate);
# subfloor_dmx and drywall_tape_mud are priced per_sqft_floor/per_sqft_surface
# instead, so _enforce_baseline_trades derives a real quantity for those two.
_BASELINE_LUMP_SUM_TRADES: dict[str, str] = {
    "plumbing_rough_and_finish": "plumbing",
    "hvac_rough_and_finish": "hvac",
    "painting": "paint",
}


def _enforce_baseline_trades(takeoff: schemas.Takeoff) -> None:
    """These five trades are near-universal for both of Company A's scopes
    (every wet bar/kitchen needs water supply+drain, every finished basement
    needs ventilation/duct tie-in, drywall, paint, and DMX subfloor -- the
    last three explicitly called out in guideline §2/§4 as standard across
    every tier) -- but none has a dedicated intake slot, so the omission
    verifier (built around slot-tied omissions) can't catch any of them
    being dropped outright. Confirmed live: across two batches of 4
    identical-spec quotes, HVAC/plumbing were each missing outright in some
    quotes (a ~$9,150 swing on a ~$50K quote), and drywall/paint showed the
    same "material priced, installation labor silently missing" pattern
    (drywall $716 material-only vs $4,117 material+labor; paint $2,930 vs
    $5,510) -- confirmed by the material-vs-material+labor arithmetic
    matching exactly. Enforced here in code, not left to prompt compliance
    -- priced normally afterward through price_fill_node's own labor-rate
    lookup, never left "unpriced"."""
    present = {ln.trade for ln in takeoff.lines if ln.trade}
    next_idx = len(takeoff.lines) + 1

    def _append(trade: str, category: str, quantity: float, unit: str) -> None:
        nonlocal next_idx
        takeoff.lines.append(schemas.TakeoffLine(
            id=f"t{next_idx}", category=category, trade=trade, quantity=quantity,
            unit=unit, description=f"{trade.replace('_', ' ')} (baseline scope)",
            basis="deterministically enforced -- every project needs baseline "
                 f"{category} rough-in/finish/install labor; the takeoff did "
                 f"not include a '{trade}' line", source="assumption"))
        next_idx += 1

    for trade, category in _BASELINE_LUMP_SUM_TRADES.items():
        if trade not in present:
            _append(trade, category, 1, "lump_sum")

    if "subfloor_dmx" not in present:
        # per_sqft_floor -- GFA is the natural, directly-applicable quantity.
        _append("subfloor_dmx", "subfloor", round(takeoff.gfa_sqft or 900.0, 1), "sqft")

    if "drywall_tape_mud" not in present:
        # per_sqft_surface (wall+ceiling), not per_sqft_floor -- derive from
        # the takeoff's OWN drywall material sheet count (1 sheet = 4x12ft =
        # 48 sqft) rather than re-estimating independently from GFA, which
        # would risk introducing a SECOND inconsistent number on top of the
        # one this function exists to fix. Only falls back to a GFA-based
        # estimate when there's no drywall material line to anchor to at all.
        sheets = sum(ln.quantity for ln in takeoff.lines
                    if ln.category == "drywall" and ln.item)
        surface_sqft = (round(sheets * 48, 1) if sheets
                       else round((takeoff.gfa_sqft or 900.0) * 1.7, 1))
        _append("drywall_tape_mud", "drywall", surface_sqft, "sqft")


# intake slot -> (trade, category, takeoff-line unit) for the trade that's
# that slot's main labor line. Unlike _BASELINE_TRADES, these only apply
# when the slot is actually filled -- a project's scope always needs
# plumbing/HVAC, but not every project necessarily has a bathroom or
# kitchen/wet bar in scope.
_SLOT_SCOPED_TRADES: dict[str, tuple[str, str, str]] = {
    "bathroom_rough_in": ("bathroom_build", "bathroom", "each"),
    "kitchen": ("kitchen_install", "kitchen", "lump_sum"),
}


def _slot_filled(value) -> bool:
    return value is not None and str(value).strip().lower() not in ("", "unknown", "none", "n/a")


def _enforce_slot_scoped_trades(takeoff: schemas.Takeoff, slots: dict) -> None:
    """bathroom_build and kitchen_install are each their category's single
    largest labor line (~$5,750 and ~$2,625 respectively) -- when the
    intake slot behind that scope is filled, the project has committed to
    it existing, so a takeoff that only prices scattered fixture allowances
    without the main build-out line understates that category by roughly
    that whole amount. The omission verifier already flags this as
    "Possible missing scope," but that still leaves it genuinely unpriced
    until the estimator resolves the flag. Confirmed live: of 4
    identical-spec quotes (bathroom_rough_in and kitchen both filled on
    every one), 2 were missing the bathroom_build line entirely and 2 were
    missing kitchen_install entirely, each swinging that category's
    subtotal by roughly its full labor-line amount. Enforced here in code,
    priced normally afterward through price_fill_node's own labor-rate
    lookup -- matches _enforce_baseline_trades' pattern, just conditioned
    on the relevant slot instead of applying unconditionally."""
    present = {ln.trade for ln in takeoff.lines if ln.trade}
    next_idx = len(takeoff.lines) + 1
    for slot, (trade, category, unit) in _SLOT_SCOPED_TRADES.items():
        if not _slot_filled(slots.get(slot)) or trade in present:
            continue
        takeoff.lines.append(schemas.TakeoffLine(
            id=f"t{next_idx}", category=category, trade=trade, quantity=1,
            unit=unit,
            description=f"{trade.replace('_', ' ')} (baseline scope, intake: "
                        f"{slot}={slots[slot]!r})",
            basis=f"deterministically enforced -- intake slot '{slot}' is "
                 f"filled, implying {category} scope exists; the takeoff "
                 f"did not include a '{trade}' line", source="assumption"))
        next_idx += 1


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
        quote_q, k=6, package_tier=tier,
        exclude_project_codes=state.get("_eval_exclude_project_codes")))
    item_keys = sorted(f"{c}/{i}" for c, i in materials.load_price_sheet())
    trade_keys = sorted({trade for trade, _ in labor.load_labor_rates()})
    allowance_keys = sorted(f"{c}/{i}" for c, i in allowances.load_allowances())
    checklist_dict = state.get("codes_checklist") or {}
    takeoff = None
    stats: list[dict] = []
    prior_issues = state.get("takeoff_issues")
    verifier_feedback = ({"issues": prior_issues, "previous_takeoff": state.get("takeoff")}
                         if prior_issues else None)
    try:
        msgs = [SystemMessage(prompts.takeoff_system()),
                HumanMessage(prompts.takeoff_user(
                    s, guidelines.section("4"), comparables,
                    checklist_dict, item_keys, trade_keys, allowance_keys,
                    state.get("estimator_feedback"), verifier_feedback))]
        m = takeoff_model()
        resp = m.invoke(msgs)
        stats.append(_usage_entry("takeoff", resp))
        takeoff = _validated(schemas.Takeoff, resp.content, "lines")
        if takeoff is None:
            retry = m.invoke(msgs + [resp, HumanMessage(_RETRY_JSON)])
            stats.append(_usage_entry("takeoff", retry))
            takeoff = _validated(schemas.Takeoff, retry.content, "lines")
    except Exception:
        logger.exception("takeoff stage failed — drafting from raw context")
    if takeoff is not None:
        for i, line in enumerate(takeoff.lines, start=1):
            line.id = f"t{i}"  # assigned in code, never by the model
        if checklist_dict.get("items"):
            checklist = schemas.CodesChecklist.model_validate(checklist_dict)
            _enforce_code_coverage(takeoff, checklist)
            _drop_non_line_item_code_refs(takeoff, checklist)
        _enforce_baseline_trades(takeoff)
        _enforce_slot_scoped_trades(takeoff, s)
    return {"takeoff": takeoff.model_dump() if takeoff else None,
            "retrieved": {**state.get("retrieved", {}),
                          "past_project_quote": comparables},
            "generation_stats": stats}


def _normalize_verify_issue(i: dict) -> dict | None:
    """Validate + backward-compatibly type one verifier issue. "contradiction"
    needs a line_id (an existing row to act on); "omission" needs a slot key
    (there's no line -- that's the whole point). Untyped issues from before
    the omission check existed default to "contradiction" when they carry a
    line_id, so old callers/tests still normalize correctly."""
    if not isinstance(i, dict):
        return None
    itype = i.get("type")
    if itype not in ("contradiction", "omission"):
        itype = "omission" if i.get("slot") and not i.get("line_id") else "contradiction"
    if itype == "omission":
        return {**i, "type": "omission"} if i.get("slot") else None
    return {**i, "type": "contradiction"} if i.get("line_id") else None


def verify_takeoff_node(state: AgentState) -> dict:
    """Second, cheap LLM pass: cross-checks the takeoff against intake slots
    and against itself for three specific failure classes -- a line implying
    new construction for scope an intake slot says already exists, a line
    contradicting another line in the same takeoff about the same physical
    item, or a filled intake slot whose implied scope has no takeoff line at
    all. Confirmed live on real quotes: the first two happened in the same
    real takeoff (egress window marked "no new window needed" on one line,
    then a new window well priced for it two lines later); the third
    happened separately (a filled bathroom-rough-in slot with zero bathroom
    lines in the generated takeoff, ~$20k of scope silently dropped).
    Degrades to "no issues" on any parse failure -- a QA-check hiccup must
    never block the pipeline."""
    takeoff = state.get("takeoff")
    attempts = state.get("takeoff_verify_attempts", 0) + 1
    issues: list[dict] = []
    stats: list[dict] = []
    if takeoff:
        try:
            msgs = [SystemMessage(prompts.takeoff_verify_system()),
                    HumanMessage(prompts.takeoff_verify_user(
                        state.get("slots", {}), takeoff))]
            resp = takeoff_verifier_model().invoke(msgs)
            stats.append(_usage_entry("takeoff_verify", resp))
            parsed = _parse_json_block(resp.content, required_key="issues")
            if parsed and isinstance(parsed.get("issues"), list):
                issues = [n for i in parsed["issues"]
                         if (n := _normalize_verify_issue(i)) is not None]
        except Exception:
            logger.exception("takeoff verification failed — proceeding without it")
    return {"takeoff_issues": issues, "takeoff_verify_attempts": attempts,
            "generation_stats": stats}


_LABOR_LUMP_UNITS = {"lump_sum"}


# per_sqft_floor trades whose rate applies to the WHOLE basement's floor
# area, not a line-specific sub-area. tiling and flooring_install_lvp are
# also per_sqft_floor but scale with a subset of the floor (the tiled zone,
# the LVP-covered area) -- their own takeoff-line quantity is already the
# right basis for that, unlike framing/subfloor_dmx which always cover the
# entire GFA regardless of how the takeoff quantified that one line.
_WHOLE_FLOOR_TRADES = {"framing", "subfloor_dmx"}


def _labor_quantity(trade: str, labor_unit: str, line_qty: float, line_unit: str,
                    gfa_sqft: float | None) -> float:
    """A takeoff line's own quantity/unit describes its MATERIAL basis (32
    drywall sheets, 569 linear ft of studs) -- but a non-lump labor rate is
    often priced on a completely different physical basis (surface sqft,
    floor sqft), and blindly reusing the material's quantity silently
    undercounts labor by whatever factor separates the two units. Confirmed
    live: a combined item+trade takeoff line priced drywall labor as 32
    (sheets) x $1.38/sqft-surface = $44.03 instead of the correct ~$2,100
    (32 sheets = 1,536 sqft surface, a fixed physical conversion -- a
    drywall_sheet_12ft sheet is 4ft x 12ft) -- a ~48x undercount. Framing
    labor was similarly computed as 569 (linear ft of studs) x $2.20/sqft-
    floor = $1,251.80 instead of the project's actual floor area x rate.

    Gating the floor-area override to _WHOLE_FLOOR_TRADES specifically
    (not just "any per_sqft_floor rate") matters: an earlier version of
    this fix applied it unconditionally and broke tiling -- a bathroom
    tile line's own 100 sqft (the tiled zone) got overridden to the
    project's full 900 sqft GFA, inflating that one line from ~$550 to
    $4,950. Falls back to `line_qty` unchanged for every other (trade,
    unit) combination -- count-based labor units (per_door, per_opening,
    per_bathroom, per_well, per_head) legitimately share the takeoff
    line's own "each"-style quantity, and that's already correct."""
    if trade in _WHOLE_FLOOR_TRADES and labor_unit == "per_sqft_floor":
        return gfa_sqft if gfa_sqft else line_qty
    if labor_unit == "per_sqft_surface" and line_unit == "sheet":
        return line_qty * 48
    return line_qty

# Takeoff line unit -> the price-sheet unit it must match before qty * price
# is trusted. Only measurement units are gated ("each"/"lump_sum" takeoff
# lines legitimately price against several different count-based sheet units
# -- per_door_cad, per_fixture_cad, per_opening_cad, per_unit_cad,
# per_bag_cad, "unit" -- so those are left unconstrained).
_MEASURED_SHEET_UNIT = {
    "sqft": "per_sqft_cad",
    "linear_ft": "per_linear_ft_cad",
    "gallon": "per_gallon_cad",
    "sheet": "per_sheet_cad",
}


def price_fill_node(state: AgentState) -> dict:
    """Deterministic price resolution — no LLM. Sheet-first (staleness-gated
    for materials, status-gated for labor, tier-gated for allowances),
    per-item web fallback when a Tavily key is present, honest 'unpriced'
    rows otherwise. Arithmetic happens here in code so the accuracy eval can
    assert it.

    A takeoff line can price against a material, a labor rate, AND a tier
    allowance — each becomes its own price_resolution row sharing
    takeoff_line_ref, never a blended number, so each dollar's source stays
    independently auditable. A spec-only allowance cell at the project's
    tier (no $ figure) falls back to the material sheet under the same
    (category, item) key rather than going straight to unpriced."""
    takeoff = state.get("takeoff") or {}
    lines = takeoff.get("lines") or []
    gfa_sqft = takeoff.get("gfa_sqft")
    gfa_band = labor.job_size_band(gfa_sqft)
    tier = _tier(state.get("slots", {}))
    rows: list[dict] = []
    charged_lump_trades: set[str] = set()
    tavily_client = None
    tavily_used = 0

    def _tavily_fallback(base: dict, desc: str, status: str) -> dict:
        nonlocal tavily_client, tavily_used
        if settings.tavily_api_key and tavily_used < 3:
            try:
                if tavily_client is None:
                    from tavily import TavilyClient
                    tavily_client = TavilyClient(api_key=settings.tavily_api_key)
                q = f"{desc} price Ontario"
                resp = tavily_client.search(q, max_results=3, include_answer=True)
                tavily_used += 1
                return {**base, "price_source": "tavily", "sheet_status": status,
                       "query": q, "answer": resp.get("answer"),
                       "results": [{"title": x["title"], "url": x["url"]}
                                   for x in resp.get("results", [])]}
            except Exception as e:
                return {**base, "price_source": "unpriced", "sheet_status": status,
                       "note": f"web price check failed ({e}) — estimator to price"}
        return {**base, "price_source": "unpriced", "sheet_status": status,
               "note": (f"no fresh sheet price ({status})"
                        + ("" if settings.tavily_api_key
                           else " and TAVILY_API_KEY not set")
                        + " — estimator to price")}

    def _price_material(cat: str, item: str, qty: float, base: dict, desc: str) -> dict:
        """(category, item) against the material price sheet; Tavily
        fallback if missing/stale. Shared by the "item" branch and the
        allowance-fallback path (a spec-only tier cell falls back to the
        material sheet under the same key)."""
        sheet_row = materials.lookup(cat, item)
        expected_sheet_unit = _MEASURED_SHEET_UNIT.get(base.get("unit", ""))
        unit_mismatch = bool(sheet_row and expected_sheet_unit
                             and sheet_row.unit != expected_sheet_unit)
        if sheet_row and not materials.is_stale(sheet_row) and not unit_mismatch:
            quoted = materials.quoted_price(sheet_row)
            return {**base, "item": item,
                   "unit_price_low_cad": sheet_row.price_low_cad,
                   "unit_price_high_cad": sheet_row.price_high_cad,
                   "unit_price_quoted_cad": quoted,
                   "extended_low_cad": round(qty * sheet_row.price_low_cad, 2),
                   "extended_high_cad": round(qty * sheet_row.price_high_cad, 2),
                   "extended_quoted_cad": round(qty * quoted, 2),
                   "sheet_unit": sheet_row.unit,
                   "price_source": "price_sheet",
                   "source_detail": (f"{sheet_row.source} (updated "
                                     f"{sheet_row.updated_at.isoformat()})"),
                   "stale": False}
        if unit_mismatch:
            # The takeoff quantified this line in a unit (e.g. sqft of paint
            # coverage) that doesn't match the sheet's pricing unit (e.g.
            # per-gallon) -- qty * sheet_row.price would silently multiply
            # the wrong basis (confirmed live: 1800 sqft x a $60/gallon paint
            # price produced a $108,000 phantom line, ~66% of that quote's
            # total, instead of the ~$900 the ~15 gallons actually needed).
            # Never guess a conversion -- fall back like a missing sheet row.
            status = f"unit_mismatch (sheet: {sheet_row.unit}, takeoff: {base.get('unit', '')})"
        else:
            status = "stale" if sheet_row else "missing"
        return _tavily_fallback({**base, "item": item}, desc or item, status)

    def _bare_key(key: str) -> str:
        """The takeoff model sometimes echoes the full "category/item" pair
        from the prompt's key lists into item/allowance_item instead of
        splitting it (observed live) — tolerate it rather than let every
        lookup silently miss."""
        return key.rsplit("/", 1)[-1] if "/" in key else key

    for line in lines:
        cat = str(line.get("category", ""))
        item = _bare_key(str(line.get("item", "") or ""))
        trade = str(line.get("trade", "") or "")
        allowance_item = _bare_key(str(line.get("allowance_item", "") or ""))
        qty = float(line.get("quantity") or 0)
        unit = line.get("unit", "")
        desc = line.get("description", "")
        base = {"category": cat, "description": desc, "quantity": qty,
                "unit": unit, "takeoff_line_ref": line.get("id", ""),
                "instance": line.get("instance", "") or ""}

        priced_anything = False
        if item and item != allowance_item:
            # When the takeoff sets both to the SAME key, "item" and
            # "allowance_item" aren't two different cost components -- they're
            # the same physical thing looked up two ways. The allowance
            # branch below already reproduces the correct price on its own
            # (falling back to this exact material-sheet row when the
            # allowance table has no distinct $ entry for the key); pricing
            # "item" here too would silently double-charge it. Confirmed
            # live across 22 real quotes / 43 lines, 6-15% of each quote's
            # total was this exact duplicate.
            priced_anything = True
            rows.append(_price_material(cat, item, qty, base, desc))

        if allowance_item:
            priced_anything = True
            arow = allowances.lookup(cat, allowance_item)
            tier_quoted = allowances.quoted_value(arow, tier) if arow else None
            if arow and tier_quoted is not None:
                lo, hi = allowances.tier_range(arow, tier)
                rows.append({**base, "allowance_item": allowance_item,
                            "unit_price_low_cad": lo, "unit_price_high_cad": hi,
                            "unit_price_quoted_cad": round(tier_quoted, 2),
                            "extended_low_cad": round(qty * lo, 2),
                            "extended_high_cad": round(qty * hi, 2),
                            "extended_quoted_cad": round(qty * tier_quoted, 2),
                            "sheet_unit": arow.unit,
                            "price_source": "allowance",
                            "source_detail": f"{arow.source} ({tier or 'unknown'} tier)"})
            else:
                # spec-only cell at this tier (or no allowance row at all) --
                # fall back to the material sheet under the same key
                rows.append({**_price_material(cat, allowance_item, qty, base, desc),
                            "allowance_item": allowance_item})

        if trade:
            priced_anything = True
            labor_row = labor.lookup(trade, gfa_band)
            if labor_row is None:
                rows.append({**base, "trade": trade, "price_source": "unpriced",
                            "sheet_status": "missing",
                            "note": (f"no labor rate found for trade '{trade}'"
                                     + ("" if gfa_band else " (GFA unknown — cannot "
                                        "determine job-size band)")
                                     + " — estimator to price")})
            else:
                is_lump = labor_row.unit in _LABOR_LUMP_UNITS
                labor_qty = qty if is_lump else _labor_quantity(trade, labor_row.unit, qty, unit, gfa_sqft)
                quoted = labor.quoted_rate(labor_row, gfa_sqft)
                ext_low = labor_row.rate_low_cad if is_lump else round(labor_qty * labor_row.rate_low_cad, 2)
                ext_high = labor_row.rate_high_cad if is_lump else round(labor_qty * labor_row.rate_high_cad, 2)
                ext_quoted = quoted if is_lump else round(labor_qty * quoted, 2)
                row = {**base, "trade": trade,
                      # Only overridden from the takeoff line's own
                      # quantity/unit when _labor_quantity actually
                      # corrected it -- otherwise identical to `base` and a
                      # no-op, preserving every already-correct case.
                      "quantity": labor_qty, "unit": labor_row.unit if labor_qty != qty else unit,
                      "unit_price_low_cad": labor_row.rate_low_cad,
                      "unit_price_high_cad": labor_row.rate_high_cad,
                      "unit_price_quoted_cad": round(quoted, 2),
                      "extended_low_cad": ext_low, "extended_high_cad": ext_high,
                      "extended_quoted_cad": round(ext_quoted, 2),
                      "sheet_unit": labor_row.unit,
                      "price_source": "labor_rate",
                      "source_detail": f"{labor_row.includes} ({labor_row.job_size_band})",
                      "rate_unverified": labor.is_rate_unverified(labor_row),
                      "site_dependent": labor.is_site_dependent(labor_row)}
                if is_lump:
                    # A lump-sum trade's rate already covers that trade's whole
                    # project scope (see its "includes" column) -- if the
                    # takeoff split that scope across multiple lines, only the
                    # first charges the lump sum; later lines are the same job,
                    # not additional cost.
                    if trade in charged_lump_trades:
                        row = {**row, "extended_low_cad": 0, "extended_high_cad": 0,
                              "extended_quoted_cad": 0,
                              "note": (f"lump-sum '{trade}' already charged on an earlier "
                                       "takeoff line — this line's labor is part of that "
                                       "same job, not billed again")}
                    else:
                        charged_lump_trades.add(trade)
                rows.append(row)

        if not priced_anything:
            rows.append({**base, "price_source": "unpriced", "sheet_status": "missing",
                        "note": "no material or labor key on this line — estimator to price"})

    # Retry-exhausted verifier flags (verify_takeoff_node): still unresolved
    # after one corrective retry. Never let either failure class reach the
    # total silently.
    issues = state.get("takeoff_issues") or []

    # Contradiction: an existing row is wrong -- neutralize it into the same
    # "unpriced" contract every other unresolved line already uses, so it
    # surfaces in the estimator's existing review flow instead of being
    # priced wrong.
    contradictions = {i["line_id"]: i.get("reason", "") for i in issues
                      if i.get("type") == "contradiction"}
    if contradictions:
        rows = [
            {**r, "price_source": "unpriced", "unit_price_low_cad": None,
             "unit_price_high_cad": None, "unit_price_quoted_cad": None,
             "extended_low_cad": None, "extended_high_cad": None,
             "extended_quoted_cad": None,
             "note": (f"takeoff verifier flagged this line: "
                      f"{contradictions[r['takeoff_line_ref']]} — estimator to "
                      "confirm before pricing")}
            if r.get("takeoff_line_ref") in contradictions else r
            for r in rows
        ]

    # Omission: a filled intake slot's scope has NO row to neutralize --
    # there's no line at all. Inject a synthetic unpriced placeholder line
    # instead, same precedent as _enforce_code_coverage's injected line for
    # a dropped mandatory code item. Confirmed live: a filled bathroom-
    # rough-in slot with zero bathroom takeoff lines, ~$20k of scope
    # silently missing with nothing in the draft to flag it.
    for i in issues:
        if i.get("type") != "omission":
            continue
        slot = i["slot"]
        rows.append({
            "category": "verifier_flagged", "description": f"Possible missing scope: {slot}",
            "quantity": 0, "unit": "lump_sum", "takeoff_line_ref": f"omission-{slot}",
            "price_source": "unpriced", "sheet_status": "missing",
            "note": (f"takeoff verifier: {i.get('reason', '')} — no takeoff line "
                     "addresses this intake slot; estimator to confirm scope and "
                     "price if applicable")})

    return {"price_resolution": rows}


def draft_node(state: AgentState) -> dict:
    """Stage 3: render the deterministic quote document (draft_render.py)
    and get the one remaining judgment call -- project summary + pricing
    confidence, per §6.2 -- from a small structured-output LLM call. Every
    dollar figure, section, heading, and citation is assembled in code from
    already-computed data; nothing about document structure is left to the
    model's discretion any more (see draft_render's module docstring for
    why that mattered)."""
    s = state.get("slots", {})
    retrieved = {**state.get("retrieved", {}),
                 "builder_guideline": _pack(get_retriever().search_guidelines(
                     f"allowances rules of thumb {_tier(s) or ''} "
                     f"{s.get('scope', 'basement')}", k=4))}
    takeoff = state.get("takeoff") or {}
    stats: list[dict] = []
    msgs = [SystemMessage(prompts.draft_narrative_system()),
            HumanMessage(prompts.draft_narrative_user(
                s, state.get("flags", []), retrieved, takeoff.get("assumptions") or []))]
    m = drafting_model()
    resp = m.invoke(msgs)
    stats.append(_usage_entry("draft", resp))
    narrative = _validated(schemas.DraftNarrative, resp.content, "project_summary")
    if narrative is None:
        retry = m.invoke(msgs + [resp, HumanMessage(_RETRY_JSON)])
        stats.append(_usage_entry("draft", retry))
        narrative = _validated(schemas.DraftNarrative, retry.content, "project_summary")
    if narrative is None:
        # Never block drafting on the narrative call -- degrade to a
        # conservative, honest default rather than fabricate confidence.
        narrative = schemas.DraftNarrative(
            project_summary="Automated summary unavailable — see intake slots below.",
            pricing_confidence="LOW",
            confidence_reasons=["narrative generation failed — defaulting to "
                                "conservative LOW confidence, verify manually"])
    draft = draft_render.render_draft({**state, "retrieved": retrieved}, narrative)
    packet = None
    if state.get("flags"):
        packet = {"route": "flag",
                  "triggers": [f["condition"] for f in state["flags"]],
                  "slots": state.get("slots", {})}
    return {"draft": draft, "routing_packet": packet,
            "estimator_feedback": None,
            "retrieved": retrieved,
            "generation_stats": stats,
            "messages": [AIMessage("Draft quote prepared — routed to the "
                                   "estimator for review before anything "
                                   "reaches you. (Draft attached below.)\n\n"
                                   + draft)]}
