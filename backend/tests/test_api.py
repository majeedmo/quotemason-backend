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
        self.price_overrides: list[dict] = []

    def create_draft(self, thread_id, draft_md, routing_packet=None,
                     stage_outputs=None, contact_email=None,
                     contact_phone=None, property_key=None):
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
               "contact_email": contact_email, "contact_phone": contact_phone,
               "property_key": property_key,
               "status": "pending_review", "estimator_edit_md": None}
        self.rows[self._id] = row
        return row

    def find_active_duplicate(self, property_key, since):
        for r in self.rows.values():
            if r["property_key"] == property_key and r["status"] != "superseded":
                return r
        return None

    def count_recent_properties_for_contact(self, email, phone, since):
        # Mirror SQL NULL semantics: a NULL parameter never matches a NULL
        # column, so an absent email/phone can't accidentally group rows.
        keys = {r["property_key"] for r in self.rows.values()
                if r["status"] != "superseded" and r["property_key"] is not None
                and ((email is not None and r["contact_email"] == email)
                     or (phone is not None and r["contact_phone"] == phone))}
        return len(keys)

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

    def record_price_override(self, thread_id, takeoff_line_ref, price_cad,
                              note, price_source_before, source_quote_id,
                              result_quote_id):
        row = {"thread_id": thread_id, "takeoff_line_ref": takeoff_line_ref,
               "price_cad": price_cad, "note": note,
               "price_source_before": price_source_before,
               "source_quote_id": source_quote_id,
               "result_quote_id": result_quote_id}
        self.price_overrides.append(row)
        return row


class FakeGraph:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def invoke(self, payload, config, **kwargs):
        self.calls.append((payload, config, kwargs))
        return self.state

    def get_state(self, config):
        return type("FakeSnapshot", (), {"values": self.state})()


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


def test_login_ok(api, monkeypatch):
    monkeypatch.setattr(settings, "estimator_demo_user", "demo")
    monkeypatch.setattr(settings, "estimator_demo_password", "demo123")
    r = api.post("/login", json={"username": "demo", "password": "demo123"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_login_wrong_password(api, monkeypatch):
    monkeypatch.setattr(settings, "estimator_demo_user", "demo")
    monkeypatch.setattr(settings, "estimator_demo_password", "demo123")
    r = api.post("/login", json={"username": "demo", "password": "nope"})
    assert r.status_code == 401


def test_login_unset_credentials_fails_closed(api, monkeypatch):
    monkeypatch.setattr(settings, "estimator_demo_user", "")
    monkeypatch.setattr(settings, "estimator_demo_password", "")
    r = api.post("/login", json={"username": "", "password": ""})
    assert r.status_code == 401


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


def test_chat_blocks_duplicate_property_before_expiry(api, monkeypatch):
    state = {**DRAFT_STATE,
             "slots": {"scope": "basement", "property_location": "123 Main St, Cambridge"}}
    _set_graph(monkeypatch, state)
    out1 = api.post("/chat", json={"thread_id": "t1", "message": "spec"}).json()
    assert out1["action"] == "complete"
    assert len(api.get("/quotes").json()) == 1

    # a different thread, same scope+address -> blocked, no second row
    _set_graph(monkeypatch, state)
    out2 = api.post("/chat", json={"thread_id": "t2", "message": "spec"}).json()
    assert out2["action"] == "duplicate_blocked"
    assert len(api.get("/quotes").json()) == 1


def test_chat_allows_different_scope_at_same_address(api, monkeypatch):
    """scope is part of the dedup key -- a kitchen quote and a separate
    basement quote at the same address are not duplicates."""
    basement = {**DRAFT_STATE,
               "slots": {"scope": "basement", "property_location": "1 Elm St"}}
    _set_graph(monkeypatch, basement)
    out1 = api.post("/chat", json={"thread_id": "t1", "message": "spec"}).json()
    assert out1["action"] == "complete"

    kitchen = {**DRAFT_STATE,
              "slots": {"scope": "kitchen", "property_location": "1 Elm St"}}
    _set_graph(monkeypatch, kitchen)
    out2 = api.post("/chat", json={"thread_id": "t2", "message": "spec"}).json()
    assert out2["action"] == "complete"
    assert len(api.get("/quotes").json()) == 2


def test_chat_rate_limits_contact_across_many_properties(api, monkeypatch):
    monkeypatch.setattr(settings, "max_quotes_per_contact_window", 2)
    contact = {"email": "Spammer@Example.com"}
    for i in range(2):
        state = {**DRAFT_STATE,
                 "slots": {"scope": "basement", "property_location": f"{i} Main St"}}
        _set_graph(monkeypatch, state)
        out = api.post("/chat", json={"thread_id": f"t{i}", "message": "spec",
                                      "contact": contact}).json()
        assert out["action"] == "complete"

    # a third distinct property from the same (case-insensitive) contact -> blocked
    state = {**DRAFT_STATE,
             "slots": {"scope": "basement", "property_location": "999 Main St"}}
    _set_graph(monkeypatch, state)
    out = api.post("/chat", json={"thread_id": "t99", "message": "spec",
                                  "contact": {"email": "spammer@example.com"}}).json()
    assert out["action"] == "rate_limited"
    assert len(api.get("/quotes").json()) == 2


def test_revise_missing_and_terminal(api, monkeypatch):
    _set_graph(monkeypatch, DRAFT_STATE)
    assert api.post("/quotes/9/revise", json={"feedback": "x"}).status_code == 404
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    api.post("/quotes/1/approve")
    assert api.post("/quotes/1/revise", json={"feedback": "x"}).status_code == 409


UNPRICED_STATE = {**DRAFT_STATE,
                  "price_resolution": [
                      {"category": "plumbing", "quantity": 1, "unit": "ea",
                       "takeoff_line_ref": "t1", "price_source": "unpriced",
                       "note": "no material or labor key on this line — "
                               "estimator to price"},
                  ]}


def _fake_draft_node(monkeypatch, captured, draft_md="# Quote v2 priced"):
    def fake(state):
        captured["price_resolution"] = state["price_resolution"]
        return {"draft": draft_md, "routing_packet": None}
    monkeypatch.setattr("app.api.main.draft_node", fake)


def test_override_price_creates_new_version(api, monkeypatch):
    """A price override does NOT go through graph.invoke (that would rebuild
    price_resolution from scratch and erase the override) -- it reads live
    state via graph.get_state and calls draft_node directly."""
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})

    captured = {}
    _fake_draft_node(monkeypatch, captured)
    out = api.post("/quotes/1/lines/t1/price",
                   json={"price_cad": 450, "note": "site visit quote"})
    assert out.status_code == 200
    row = out.json()
    assert (row["version"], row["draft_md"]) == (2, "# Quote v2 priced")

    overridden = captured["price_resolution"][0]
    assert overridden["price_source"] == "estimator_override"
    assert overridden["extended_quoted_cad"] == 450
    assert overridden["unit_price_quoted_cad"] == 450.0
    assert overridden["note"] == "site visit quote"
    assert row["stage_outputs"]["price_resolution"][0]["price_source"] == "estimator_override"

    # old version superseded, audit row recorded
    assert api.get("/quotes/1").json()["status"] == "superseded"
    audit = api.fake_store.price_overrides[0]
    assert audit == {"thread_id": "t1", "takeoff_line_ref": "t1",
                     "price_cad": 450, "note": "site visit quote",
                     "price_source_before": "unpriced",
                     "source_quote_id": 1, "result_quote_id": 2}


def test_override_price_missing_quote(api, monkeypatch):
    _set_graph(monkeypatch, UNPRICED_STATE)
    assert api.post("/quotes/9/lines/t1/price",
                    json={"price_cad": 10}).status_code == 404


def test_override_price_terminal_quote_rejected(api, monkeypatch):
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    api.post("/quotes/1/approve")
    assert api.post("/quotes/1/lines/t1/price",
                    json={"price_cad": 10}).status_code == 409


def test_override_price_unknown_line_ref(api, monkeypatch):
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    assert api.post("/quotes/1/lines/does-not-exist/price",
                    json={"price_cad": 10}).status_code == 404


def test_override_price_already_priced_line_rejected(api, monkeypatch):
    """Capstone scope: only lines still marked 'unpriced' are eligible --
    this is the one guard clause to relax when overrides open up to any
    line."""
    priced_state = {**DRAFT_STATE,
                    "price_resolution": [
                        {"category": "flooring", "item": "lvp", "quantity": 900,
                         "unit": "sqft", "takeoff_line_ref": "t1",
                         "price_source": "price_sheet"},
                    ]}
    _set_graph(monkeypatch, priced_state)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    assert api.post("/quotes/1/lines/t1/price",
                    json={"price_cad": 10}).status_code == 409


def test_override_price_ambiguous_unpriced_rows_rejected(api, monkeypatch):
    ambiguous_state = {**DRAFT_STATE,
                       "price_resolution": [
                           {"category": "plumbing", "quantity": 1, "unit": "ea",
                            "takeoff_line_ref": "t1", "price_source": "unpriced",
                            "trade": "plumber",
                            "note": "no labor rate found — estimator to price"},
                           {"category": "plumbing", "quantity": 1, "unit": "ea",
                            "takeoff_line_ref": "t1", "price_source": "unpriced",
                            "allowance_item": "fixture",
                            "note": "no allowance on file — estimator to price"},
                       ]}
    _set_graph(monkeypatch, ambiguous_state)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    assert api.post("/quotes/1/lines/t1/price",
                    json={"price_cad": 10}).status_code == 409


def test_override_price_checkpointer_state_unavailable(api, monkeypatch):
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    _set_graph(monkeypatch, {})
    assert api.post("/quotes/1/lines/t1/price",
                    json={"price_cad": 10}).status_code == 409
