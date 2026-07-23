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
from urllib.parse import quote as _urlquote

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from app.api import deps
from app.config import settings

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


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _stage_outputs(state: dict) -> dict | None:
    """The pipeline's structured outputs, persisted with the draft — the
    quote-accuracy eval asserts against these, not the prose."""
    out = {k: state.get(k)
           for k in ("codes_checklist", "takeoff", "price_resolution")}
    return out if any(out.values()) else None


def _finish_draft(thread_id: str, contact: dict[str, str] | None) -> None:
    """Resume the graph interrupted after intake (codes -> takeoff ->
    price_fill -> draft) and persist the result for the estimator queue.
    Runs as a background task — the customer never waits on (or sees) the
    draft."""
    try:
        state = deps.get_graph().invoke(
            None, {"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("background drafting failed (thread %s)", thread_id)
        return
    if not state.get("draft"):
        logger.error("background drafting produced no draft (thread %s)",
                     thread_id)
        return
    packet = state.get("routing_packet") or {}
    if contact:
        packet = {**packet, "contact": contact}
    deps.get_store().create_draft(thread_id, state["draft"], packet or None,
                                  stage_outputs=_stage_outputs(state))


@app.post("/chat")
def chat(body: ChatIn, background: BackgroundTasks):
    """One customer intake turn. When the turn completes intake, the graph is
    paused before retrieval and the reply returns immediately — drafting
    finishes in the background and lands in the review queue."""
    state = deps.get_graph().invoke(
        {"messages": [HumanMessage(body.message)]},
        {"configurable": {"thread_id": body.thread_id}},
        interrupt_before=["codes"])
    if state.get("_action") == "complete":
        background.add_task(_finish_draft, body.thread_id, body.contact)
    return {"reply": state["messages"][-1].content,
            "action": state.get("_action"),
            "trigger_level": (state.get("trigger") or {}).get("level"),
            "routing_packet": state.get("routing_packet"),
            "quote_id": None}


@app.get("/quotes")
def list_quotes(status: str | None = None):
    return deps.get_store().list(status)


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
    state = deps.get_graph().invoke(
        {"messages": [HumanMessage(f"[estimator revision request] "
                                   f"{body.feedback}")],
         "estimator_feedback": body.feedback},
        {"configurable": {"thread_id": row["thread_id"]}})
    if not state.get("draft"):
        raise HTTPException(502, "revision run produced no draft")
    packet = state.get("routing_packet") or {}
    prev_contact = (row.get("routing_packet") or {}).get("contact")
    if prev_contact:
        packet = {**packet, "contact": prev_contact}
    return store.create_draft(row["thread_id"], state["draft"], packet or None,
                              stage_outputs=_stage_outputs(state))
