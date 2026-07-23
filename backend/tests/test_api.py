"""API tests — no network: fake graph, in-memory fake store, LangSmith off."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.main import app
from app.config import settings


class FakeStore:
    """In-memory stand-in mirroring QuoteStore semantics."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0

    def create_draft(self, thread_id, draft_md, routing_packet=None,
                     stage_outputs=None):
        for r in self.rows.values():
            if (r["thread_id"] == thread_id
                    and r["status"] in ("pending_review", "edited")):
                r["status"] = "superseded"
        self._id += 1
        version = 1 + max((r["version"] for r in self.rows.values()
                           if r["thread_id"] == thread_id), default=0)
        row = {"id": self._id, "thread_id": thread_id, "version": version,
               "draft_md": draft_md, "routing_packet": routing_packet,
               "stage_outputs": stage_outputs,
               "status": "pending_review", "estimator_edit_md": None}
        self.rows[self._id] = row
        return row

    def get(self, quote_id):
        return self.rows.get(quote_id)

    def list(self, status=None):
        return [r for r in self.rows.values()
                if status is None or r["status"] == status]

    def save_edit(self, quote_id, edited_md):
        r = self.rows.get(quote_id)
        if r is None or r["status"] not in ("pending_review", "edited"):
            return None
        r.update(estimator_edit_md=edited_md, status="edited")
        return r

    def approve(self, quote_id):
        r = self.rows.get(quote_id)
        if r is None or r["status"] not in ("pending_review", "edited"):
            return None
        r["status"] = "approved"
        return r


class FakeGraph:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def invoke(self, payload, config, **kwargs):
        self.calls.append((payload, config, kwargs))
        return self.state


@pytest.fixture
def api(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(deps, "get_store", lambda: store)
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    client = TestClient(app)
    client.fake_store = store
    return client


def _set_graph(monkeypatch, state):
    g = FakeGraph(state)
    monkeypatch.setattr(deps, "get_graph", lambda: g)
    return g


DRAFT_STATE = {"messages": [type("M", (), {"content": "Draft attached"})()],
               "_action": "complete", "draft": "# Quote v1",
               "trigger": {"level": "clear"}, "routing_packet": None,
               "codes_checklist": {"items": []},
               "takeoff": {"lines": [{"category": "flooring", "item": "lvp",
                                      "quantity": 900, "unit": "sqft"}]},
               "price_resolution": [{"category": "flooring", "item": "lvp",
                                     "price_source": "price_sheet"}]}


def test_chat_complete_persists_draft(api, monkeypatch):
    """Intake completion replies immediately (no quote_id yet) and the
    background task resumes the interrupted graph and persists the draft
    (TestClient runs background tasks before returning)."""
    g = _set_graph(monkeypatch, DRAFT_STATE)
    out = api.post("/chat", json={"thread_id": "t1", "message": "spec"}).json()
    assert out["quote_id"] is None and out["action"] == "complete"
    row = api.get("/quotes/1").json()
    assert row["draft_md"] == "# Quote v1" and row["status"] == "pending_review"
    # the structured stage outputs ride along for the accuracy eval
    assert row["stage_outputs"]["takeoff"]["lines"][0]["item"] == "lvp"
    # first call is the intake turn paused before the pipeline; second resumes
    assert g.calls[0][2] == {"interrupt_before": ["codes"]}
    assert g.calls[1][0] is None


def test_chat_contact_lands_in_packet_and_mailto(api, monkeypatch):
    _set_graph(monkeypatch, DRAFT_STATE)
    contact = {"email": "client@example.com", "name": "Pat"}
    api.post("/chat", json={"thread_id": "t1", "message": "spec",
                            "contact": contact})
    row = api.get("/quotes/1").json()
    assert row["routing_packet"]["contact"] == contact
    # a revision carries the contact forward onto the new version
    _set_graph(monkeypatch, {**DRAFT_STATE, "draft": "# Quote v2"})
    v2 = api.post("/quotes/1/revise", json={"feedback": "tweak"}).json()
    assert v2["routing_packet"]["contact"] == contact
    approved = api.post(f"/quotes/{v2['id']}/approve").json()
    assert approved["mailto_url"].startswith("mailto:client@example.com?")


def test_chat_ask_persists_nothing(api, monkeypatch):
    _set_graph(monkeypatch, {**DRAFT_STATE, "_action": "ask", "draft": None})
    out = api.post("/chat", json={"thread_id": "t1", "message": "hi"}).json()
    assert out["quote_id"] is None
    assert api.get("/quotes").json() == []


def test_edit_then_approve_uses_edited_body(api, monkeypatch):
    _set_graph(monkeypatch, DRAFT_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    edited = api.post("/quotes/1/edit", json={"edited_md": "# Quote v1 fixed"})
    assert edited.json()["status"] == "edited"
    approved = api.post("/quotes/1/approve").json()
    assert approved["quote"]["status"] == "approved"
    assert "Quote%20v1%20fixed" in approved["mailto_url"]
    # terminal states reject further mutation
    assert api.post("/quotes/1/edit", json={"edited_md": "x"}).status_code == 409
    assert api.post("/quotes/1/approve").status_code == 409


def test_revise_creates_v2_and_supersedes_v1(api, monkeypatch):
    _set_graph(monkeypatch, DRAFT_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    g = _set_graph(monkeypatch, {**DRAFT_STATE, "draft": "# Quote v2"})
    out = api.post("/quotes/1/revise", json={"feedback": "drop the sauna"})
    assert out.status_code == 200
    row = out.json()
    assert (row["version"], row["draft_md"]) == (2, "# Quote v2")
    assert api.get("/quotes/1").json()["status"] == "superseded"
    # graph was resumed on the same thread with the feedback flag set
    payload, config, _ = g.calls[0]
    assert payload["estimator_feedback"] == "drop the sauna"
    assert config["configurable"]["thread_id"] == "t1"


def test_revise_missing_and_terminal(api, monkeypatch):
    _set_graph(monkeypatch, DRAFT_STATE)
    assert api.post("/quotes/9/revise", json={"feedback": "x"}).status_code == 404
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    api.post("/quotes/1/approve")
    assert api.post("/quotes/1/revise", json={"feedback": "x"}).status_code == 409
