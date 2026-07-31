"""FastAPI surface: customer intake chat + the estimator review gate.

Run (from backend/):
    uv run uvicorn app.api.main:app --reload

The agent never sends anything to a client. /approve returns a mailto: URL —
the stand-in send action this build ships — which the estimator triggers
manually. Every estimator edit is logged to LangSmith (best-effort): each one
is a labeled example of what the agent got wrong.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote as _urlquote

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from app.agent.nodes import _generation_summary, draft_node
from app.api import deps
from app.config import settings
from app.guardrails import check_duplicate, normalize_email, normalize_phone, property_key
from app.quotes.store import ACTIVE_STATUSES

app = FastAPI(title="QuoteMason API",
              description="Agentic estimation assistant — intake chat and "
                          "estimator review gate")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatIn(BaseModel):
    thread_id: str
    message: str
    # {email, name?, phone?} captured by the intake gate before the chat
    # starts; carried into the routing packet so the estimator knows where
    # the approved quote goes.
    contact: dict[str, str] | None = None


class EditIn(BaseModel):
    edited_md: str


class ReviseIn(BaseModel):
    feedback: str


class PriceOverrideIn(BaseModel):
    # ge=0, not gt=0 -- $0 is a legitimate estimator-entered price (e.g. an
    # item already covered elsewhere, or a code-compliance line with no
    # cost), confirmed live to be rejected outright by pydantic before this
    # even reached the handler.
    price_cad: float = Field(ge=0)
    note: str | None = None


class PriceOverrideItem(BaseModel):
    takeoff_line_ref: str
    price_cad: float = Field(ge=0)
    note: str | None = None


class PriceOverridesIn(BaseModel):
    overrides: list[PriceOverrideItem] = Field(min_length=1)


class LoginIn(BaseModel):
    username: str
    password: str


def _log_edit_to_langsmith(row: dict, edited_md: str) -> None:
    """Estimator edits are labeled eval data (brief, Task 5/6). Best-effort."""
    if not settings.langsmith_api_key:
        return
    try:
        from langsmith import Client
        Client(api_key=settings.langsmith_api_key).create_run(
            name="estimator_edit", run_type="chain",
            project_name=settings.langsmith_project,
            inputs={"thread_id": row["thread_id"], "version": row["version"],
                    "draft_md": row["draft_md"]},
            outputs={"estimator_edit_md": edited_md},
            tags=["estimator_edit", f"thread={row['thread_id']}"])
    except Exception:
        pass


def _log_price_override_to_langsmith(row: dict, takeoff_line_ref: str,
                                     price_cad: float, note: str | None) -> None:
    """Same eval-logging contract as _log_edit_to_langsmith, for single-line
    price overrides."""
    if not settings.langsmith_api_key:
        return
    try:
        from langsmith import Client
        Client(api_key=settings.langsmith_api_key).create_run(
            name="estimator_price_override", run_type="chain",
            project_name=settings.langsmith_project,
            inputs={"thread_id": row["thread_id"], "version": row["version"],
                    "takeoff_line_ref": takeoff_line_ref},
            outputs={"price_cad": price_cad, "note": note},
            tags=["estimator_price_override", f"thread={row['thread_id']}"])
    except Exception:
        pass


def _log_price_overrides_batch_to_langsmith(row: dict, overrides: list) -> None:
    if not settings.langsmith_api_key:
        return
    try:
        from langsmith import Client
        Client(api_key=settings.langsmith_api_key).create_run(
            name="estimator_price_overrides_batch", run_type="chain",
            project_name=settings.langsmith_project,
            inputs={"thread_id": row["thread_id"], "version": row["version"],
                    "takeoff_line_refs": [o.takeoff_line_ref for o in overrides]},
            outputs={"overrides": [{"takeoff_line_ref": o.takeoff_line_ref,
                                    "price_cad": o.price_cad, "note": o.note}
                                   for o in overrides]},
            tags=["estimator_price_override", "batch", f"thread={row['thread_id']}"])
    except Exception:
        pass


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/login")
def login(body: LoginIn):
    """Demo-only gate for the frontend's /dashboard — not real auth (see
    Settings.estimator_demo_user/password). No session/token issued; the
    frontend just flips a sessionStorage flag on success."""
    if (body.username != settings.estimator_demo_user
            or body.password != settings.estimator_demo_password
            or not settings.estimator_demo_user):
        raise HTTPException(401, "invalid username or password")
    return {"ok": True}


def _needs_price(row: dict) -> bool:
    """A price_resolution row the pipeline couldn't usably price -- either
    explicitly "unpriced", or a Tavily fallback that returned narrative
    text with no parseable number (seen live on quote #24: "Cabinetry --
    material/supply" came back price_source "tavily" with an "answer"
    string but no extended_quoted_cad, and the drafting LLM correctly
    wrote "estimator to price" for it -- the structured data just didn't
    say so)."""
    if row.get("price_source") == "unpriced":
        return True
    return (row.get("price_source") == "tavily"
            and row.get("extended_quoted_cad") is None)


def _find_priceable_row(price_resolution: list[dict], ref: str) -> dict:
    """The single priceable row for `ref`, or raises the matching
    HTTPException (404 no such line, 409 already priced, 409 ambiguous --
    a takeoff line can produce several price_resolution rows sharing one
    ref, and in rare cases more than one can independently need a price)."""
    matches = [r for r in price_resolution if r.get("takeoff_line_ref") == ref]
    if not matches:
        raise HTTPException(404, f"no such takeoff line in this quote: {ref}")
    eligible = [r for r in matches if _needs_price(r)]
    if len(eligible) > 1:
        raise HTTPException(409, f"ambiguous line {ref}: {len(eligible)} "
                                  "rows need a price, cannot determine which")
    if not eligible:
        raise HTTPException(409, f"line already priced: {ref}")
    return eligible[0]


def _apply_price_override(row: dict, price_cad: float, note: str | None) -> dict:
    qty = row.get("quantity") or 0
    return {**row, "price_source": "estimator_override",
            "unit_price_quoted_cad": round(price_cad / qty, 2) if qty else None,
            "extended_quoted_cad": price_cad,
            "source_detail": "estimator-provided price", "note": note}


def _load_active_quote_state(quote_id: int) -> tuple[dict, dict]:
    """Shared prelude for the two price-override routes: the quote row
    (404/409-gated) plus its live checkpointed AgentState (409 if the
    checkpointer no longer has it)."""
    store = deps.get_store()
    row = store.get(quote_id)
    if row is None:
        raise HTTPException(404, "quote not found")
    if row["status"] not in ACTIVE_STATUSES:
        raise HTTPException(409, f"quote is {row['status']}, not editable")

    snapshot = deps.get_graph().get_state(
        {"configurable": {"thread_id": row["thread_id"]}})
    state = dict(snapshot.values) if snapshot and snapshot.values else {}
    if not state:
        raise HTTPException(
            409, "conversation state no longer available for this quote "
                 "(checkpointer may have evicted it) -- use /edit or /revise "
                 "instead")
    return row, state


def _stage_outputs(state: dict) -> dict | None:
    """The pipeline's structured outputs, persisted with the draft — the
    quote-accuracy eval asserts against these, not the prose."""
    out = {k: state.get(k)
           for k in ("codes_checklist", "takeoff", "price_resolution")}
    if not any(out.values()):
        return None
    stats = state.get("generation_stats") or []
    out["generation_stats"] = stats
    out["generation_summary"] = _generation_summary(stats)
    return out


def _record_generation_event(new_row: dict, trigger: str, duration_seconds: float,
                             event_stats: list[dict]) -> None:
    """Best-effort dashboard row for this generation event's own incremental
    cost/usage -- never let a stats-recording hiccup block the draft that
    was already successfully created from being returned."""
    try:
        summary = _generation_summary(event_stats)
        deps.get_store().record_generation_event(
            quote_id=new_row["id"], thread_id=new_row["thread_id"],
            version=new_row["version"], trigger=trigger,
            duration_seconds=duration_seconds,
            total_cost_usd=summary["total_cost_usd"],
            cost_is_complete=summary["cost_is_complete"],
            total_input_tokens=summary["total_input_tokens"],
            total_output_tokens=summary["total_output_tokens"],
            llm_calls=summary["llm_calls"])
    except Exception:
        logger.exception("failed to record generation event for quote %s",
                         new_row.get("id"))


def _sync_generation_stats_to_checkpointer(thread_id: str, event_stats: list[dict]) -> None:
    """Price-override endpoints call draft_node directly (bypassing
    graph.invoke), so the checkpointer never learns about this event's own
    new generation_stats entries -- without this, a LATER override/revision
    on the same thread reads stale prior state and undercounts this one's
    contribution to the cumulative total (confirmed live: a second price
    override on a thread silently dropped the first one's own cost from the
    displayed cumulative figure). Best-effort: the draft is already
    successfully created and returned regardless of whether this succeeds."""
    if not event_stats:
        return
    try:
        deps.get_graph().update_state(
            {"configurable": {"thread_id": thread_id}},
            {"generation_stats": event_stats})
    except Exception:
        logger.exception("failed to sync generation_stats to checkpointer "
                         "for thread %s", thread_id)


def _log_guardrail_event(reason: str, thread_id: str,
                         property_key_val: str | None) -> None:
    """Best-effort LangSmith tag when a guardrail blocks a request — the
    "alert" an estimator can see by checking LangSmith traces. No raw
    email/phone here: thread_id is enough to find the conversation."""
    if not settings.langsmith_api_key:
        return
    try:
        from langsmith import Client
        Client(api_key=settings.langsmith_api_key).create_run(
            name="guardrail_blocked", run_type="chain",
            project_name=settings.langsmith_project,
            inputs={"thread_id": thread_id, "property_key": property_key_val},
            outputs={"reason": reason},
            tags=["guardrail_blocked", f"reason={reason}", f"thread={thread_id}"])
    except Exception:
        pass


def _finish_draft(thread_id: str, contact: dict[str, str] | None) -> None:
    """Resume the graph interrupted after intake (codes -> takeoff ->
    price_fill -> draft) and persist the result for the estimator queue.
    Runs as a background task — the customer never waits on (or sees) the
    draft."""
    config = {"configurable": {"thread_id": thread_id}}
    prior_snapshot = deps.get_graph().get_state(config)
    prior_len = len((prior_snapshot.values if prior_snapshot else {})
                    .get("generation_stats") or [])
    started = time.monotonic()
    try:
        state = deps.get_graph().invoke(None, config)
    except Exception:
        logger.exception("background drafting failed (thread %s)", thread_id)
        return
    if not state.get("draft"):
        logger.error("background drafting produced no draft (thread %s)",
                     thread_id)
        return
    duration = round(time.monotonic() - started, 1)
    # This event's own contribution -- everything appended since before this
    # invocation, not the whole thread's cumulative history (generation_stats
    # is strictly append-only, so this slice is safe).
    event_stats = (state.get("generation_stats") or [])[prior_len:]
    packet = state.get("routing_packet") or {}
    if contact:
        packet = {**packet, "contact": contact}
    contact = contact or {}
    slots = state.get("slots", {})
    stage_outputs = _stage_outputs(state) or {}
    new_row = deps.get_store().create_draft(
        thread_id, state["draft"], packet or None,
        stage_outputs={**stage_outputs, "generation_duration_seconds": duration},
        contact_email=normalize_email(contact.get("email")),
        contact_phone=normalize_phone(contact.get("phone")),
        property_key=property_key(slots.get("scope"), slots.get("property_location")))
    _record_generation_event(new_row, "initial", duration, event_stats)


@app.post("/chat")
def chat(body: ChatIn, background: BackgroundTasks):
    """One customer intake turn. When the turn completes intake, the graph is
    paused before retrieval and the reply returns immediately — drafting
    finishes in the background and lands in the review queue. A duplicate-
    quote guardrail check runs first (same property already active, or this
    contact starting too many properties too fast) — a blocked request skips
    drafting entirely rather than spending LLM/Tavily budget on a draft that
    would just get discarded."""
    state = deps.get_graph().invoke(
        {"messages": [HumanMessage(body.message)]},
        {"configurable": {"thread_id": body.thread_id}},
        interrupt_before=["codes"])
    action = state.get("_action")
    if action == "complete":
        slots = state.get("slots", {})
        reason = check_duplicate(deps.get_store(), slots, body.contact)
        if reason:
            _log_guardrail_event(reason, body.thread_id,
                                 property_key(slots.get("scope"),
                                             slots.get("property_location")))
            action = ("duplicate_blocked" if reason == "duplicate_property"
                      else "rate_limited")
        else:
            background.add_task(_finish_draft, body.thread_id, body.contact)
    return {"reply": state["messages"][-1].content,
            "action": action,
            "trigger_level": (state.get("trigger") or {}).get("level"),
            "routing_packet": state.get("routing_packet"),
            "quote_id": None}


@app.get("/quotes")
def list_quotes(status: str | None = None):
    return deps.get_store().list(status)


@app.get("/quotes/generation-stats")
def generation_stats(days: int = 30):
    """Dashboard widget data: aggregate totals + recent generation events
    over the trailing window. Backed by quote_generation_events, not
    LangSmith -- LangSmith is optional/best-effort everywhere else in this
    app and its cost field is documented as unreliable on repeat reads,
    exactly the access pattern a dashboard needs."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return deps.get_store().generation_dashboard_stats(since)


@app.get("/quotes/{quote_id}")
def get_quote(quote_id: int):
    row = deps.get_store().get(quote_id)
    if row is None:
        raise HTTPException(404, "quote not found")
    return row


@app.post("/quotes/{quote_id}/edit")
def edit_quote(quote_id: int, body: EditIn):
    store = deps.get_store()
    before = store.get(quote_id)
    if before is None:
        raise HTTPException(404, "quote not found")
    row = store.save_edit(quote_id, body.edited_md)
    if row is None:
        raise HTTPException(409, f"quote is {before['status']}, not editable")
    _log_edit_to_langsmith(before, body.edited_md)
    return row


@app.post("/quotes/{quote_id}/approve")
def approve_quote(quote_id: int):
    """Approve for sending. Returns a mailto: URL (stand-in send action) —
    the estimator sends it themselves; there is no auto-send path."""
    store = deps.get_store()
    before = store.get(quote_id)
    if before is None:
        raise HTTPException(404, "quote not found")
    row = store.approve(quote_id)
    if row is None:
        raise HTTPException(409, f"quote is {before['status']}, not approvable")
    body_md = row["estimator_edit_md"] or row["draft_md"]
    contact = (row.get("routing_packet") or {}).get("contact") or {}
    recipient = _urlquote(contact.get("email", ""), safe="@")
    mailto = (f"mailto:{recipient}?subject="
              + _urlquote(f"Your renovation quote (ref {row['thread_id']})")
              + "&body=" + _urlquote(body_md))
    return {"quote": row, "mailto_url": mailto}


@app.post("/quotes/{quote_id}/revise")
def revise_quote(quote_id: int, body: ReviseIn):
    """Estimator requests changes: resume the same conversation thread with
    the feedback (skipping intake), persist the new draft as version n+1."""
    store = deps.get_store()
    row = store.get(quote_id)
    if row is None:
        raise HTTPException(404, "quote not found")
    if row["status"] not in ("pending_review", "edited"):
        raise HTTPException(409, f"quote is {row['status']}, not revisable")
    config = {"configurable": {"thread_id": row["thread_id"]}}
    prior_snapshot = deps.get_graph().get_state(config)
    prior_len = len((prior_snapshot.values if prior_snapshot else {})
                    .get("generation_stats") or [])
    started = time.monotonic()
    state = deps.get_graph().invoke(
        {"messages": [HumanMessage(f"[estimator revision request] "
                                   f"{body.feedback}")],
         "estimator_feedback": body.feedback},
        config)
    if not state.get("draft"):
        raise HTTPException(502, "revision run produced no draft")
    duration = round(time.monotonic() - started, 1)
    event_stats = (state.get("generation_stats") or [])[prior_len:]
    packet = state.get("routing_packet") or {}
    prev_contact = (row.get("routing_packet") or {}).get("contact")
    if prev_contact:
        packet = {**packet, "contact": prev_contact}
    # Same thread/job as the row being revised — carry its identity columns
    # forward rather than recomputing (intake doesn't re-run on a revision).
    stage_outputs = _stage_outputs(state) or {}
    new_row = store.create_draft(
        row["thread_id"], state["draft"], packet or None,
        stage_outputs={**stage_outputs, "generation_duration_seconds": duration},
        contact_email=row.get("contact_email"),
        contact_phone=row.get("contact_phone"),
        property_key=row.get("property_key"))
    _record_generation_event(new_row, "revise", duration, event_stats)
    return new_row


@app.post("/quotes/{quote_id}/lines/{takeoff_line_ref}/price")
def override_line_price(quote_id: int, takeoff_line_ref: str, body: PriceOverrideIn):
    """Estimator prices a single line the pipeline couldn't usably price.
    Unlike /revise, this does NOT re-run codes/takeoff/price_fill -- those
    would just rebuild price_resolution from scratch and erase the override.
    Instead it patches the one row in the live checkpointed state and
    re-invokes draft_node directly, so only the drafting LLM call is spent."""
    store = deps.get_store()
    row, state = _load_active_quote_state(quote_id)
    price_resolution = state.get("price_resolution") or []
    target = _find_priceable_row(price_resolution, takeoff_line_ref)
    price_source_before = target.get("price_source", "unpriced")

    override_row = _apply_price_override(target, body.price_cad, body.note)
    updated_resolution = [
        override_row if r is target else r for r in price_resolution]

    started = time.monotonic()
    result = draft_node({**state, "price_resolution": updated_resolution})
    if not result.get("draft"):
        raise HTTPException(502, "draft regeneration produced no draft")
    duration = round(time.monotonic() - started, 1)
    # draft_node is called directly here (not via graph.invoke), so it
    # returns only its OWN new entries already -- no slicing needed, unlike
    # the full-graph paths above.
    event_stats = result.get("generation_stats") or []
    cumulative_stats = (state.get("generation_stats") or []) + event_stats

    new_row = store.create_draft(
        row["thread_id"], result["draft"], result.get("routing_packet"),
        stage_outputs={"codes_checklist": state.get("codes_checklist"),
                       "takeoff": state.get("takeoff"),
                       "price_resolution": updated_resolution,
                       "generation_stats": cumulative_stats,
                       "generation_summary": _generation_summary(cumulative_stats),
                       "generation_duration_seconds": duration},
        contact_email=row.get("contact_email"),
        contact_phone=row.get("contact_phone"),
        property_key=row.get("property_key"))
    store.record_price_override(
        thread_id=row["thread_id"], takeoff_line_ref=takeoff_line_ref,
        price_cad=body.price_cad, note=body.note,
        price_source_before=price_source_before,
        source_quote_id=quote_id, result_quote_id=new_row["id"])
    _record_generation_event(new_row, "price_override", duration, event_stats)
    _sync_generation_stats_to_checkpointer(row["thread_id"], event_stats)
    _log_price_override_to_langsmith(row, takeoff_line_ref, body.price_cad,
                                     body.note)
    return new_row


@app.post("/quotes/{quote_id}/lines/price")
def override_line_prices(quote_id: int, body: PriceOverridesIn):
    """Batch form of override_line_price: prices N lines in one call,
    producing exactly one new draft version instead of N. All-or-nothing --
    if any submitted line is invalid (not found, already priced, or
    ambiguous), the whole request is rejected and no version is created."""
    store = deps.get_store()
    row, state = _load_active_quote_state(quote_id)
    price_resolution = state.get("price_resolution") or []

    refs = [item.takeoff_line_ref for item in body.overrides]
    if len(refs) != len(set(refs)):
        raise HTTPException(400, "duplicate takeoff_line_ref in one request")

    # Validate every item up front -- nothing is applied until all pass, so
    # this is naturally all-or-nothing.
    targets = [(item, _find_priceable_row(price_resolution, item.takeoff_line_ref))
               for item in body.overrides]

    updated_resolution = list(price_resolution)
    price_sources_before = {}
    for item, target in targets:
        price_sources_before[item.takeoff_line_ref] = target.get("price_source", "unpriced")
        override_row = _apply_price_override(target, item.price_cad, item.note)
        updated_resolution = [
            override_row if r is target else r for r in updated_resolution]

    started = time.monotonic()
    result = draft_node({**state, "price_resolution": updated_resolution})
    if not result.get("draft"):
        raise HTTPException(502, "draft regeneration produced no draft")
    duration = round(time.monotonic() - started, 1)
    event_stats = result.get("generation_stats") or []
    cumulative_stats = (state.get("generation_stats") or []) + event_stats

    new_row = store.create_draft(
        row["thread_id"], result["draft"], result.get("routing_packet"),
        stage_outputs={"codes_checklist": state.get("codes_checklist"),
                       "takeoff": state.get("takeoff"),
                       "price_resolution": updated_resolution,
                       "generation_stats": cumulative_stats,
                       "generation_summary": _generation_summary(cumulative_stats),
                       "generation_duration_seconds": duration},
        contact_email=row.get("contact_email"),
        contact_phone=row.get("contact_phone"),
        property_key=row.get("property_key"))
    for item, _target in targets:
        store.record_price_override(
            thread_id=row["thread_id"], takeoff_line_ref=item.takeoff_line_ref,
            price_cad=item.price_cad, note=item.note,
            price_source_before=price_sources_before[item.takeoff_line_ref],
            source_quote_id=quote_id, result_quote_id=new_row["id"])
    _record_generation_event(new_row, "price_override", duration, event_stats)
    _sync_generation_stats_to_checkpointer(row["thread_id"], event_stats)
    _log_price_overrides_batch_to_langsmith(row, body.overrides)
    return new_row
