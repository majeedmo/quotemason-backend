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

    def record_generation_event(self, **kwargs):
        self.generation_events = getattr(self, "generation_events", [])
        self.generation_events.append(kwargs)
        return kwargs

    def generation_dashboard_stats(self, since, limit=10):
        events = [e for e in getattr(self, "generation_events", [])]
        total_cost = sum(e["total_cost_usd"] or 0 for e in events)
        return {
            "totals": {"count": len(events), "total_cost_usd": total_cost,
                      "avg_duration_seconds": (sum(e["duration_seconds"] for e in events) / len(events)
                                               if events else 0),
                      "avg_cost_usd": (total_cost / len(events) if events else 0)},
            "recent": list(reversed(events))[:limit],
        }


class FakeGraph:
    def __init__(self, state):
        self.state = state
        self.calls = []
        # get_state() defaults to returning the same `state` as invoke() --
        # matches every existing test's assumption (e.g. the price-override
        # endpoints read current price_resolution/takeoff via get_state()).
        # Tests that need get_state() to return something DIFFERENT from
        # what invoke() returns (to exercise main.py's before/after
        # generation_stats delta-slicing) can set this explicitly.
        self.get_state_override: dict | None = None

    def invoke(self, payload, config, **kwargs):
        self.calls.append((payload, config, kwargs))
        return self.state

    def get_state(self, config):
        values = self.state if self.get_state_override is None else self.get_state_override
        return type("FakeSnapshot", (), {"values": values})()

    def update_state(self, config, values):
        # Mirrors the real operator.add reducer for generation_stats
        # specifically (confirmed against a real, isolated LangGraph
        # checkpointer before relying on this in main.py): APPEND, don't
        # overwrite. Applied to get_state_override so a subsequent
        # get_state() call sees it, matching how a real checkpoint update
        # is visible to the next read.
        self.update_state_calls = getattr(self, "update_state_calls", [])
        self.update_state_calls.append(values)
        base = dict(self.get_state_override) if self.get_state_override is not None else dict(self.state)
        if "generation_stats" in values:
            base["generation_stats"] = (base.get("generation_stats") or []) + values["generation_stats"]
        self.get_state_override = base


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
                                     "price_source": "price_sheet"}],
               "generation_stats": [
                   {"stage": "codes", "model": "anthropic/claude-haiku-4.5",
                    "input_tokens": 500, "output_tokens": 100, "cost_usd": 0.001},
                   {"stage": "draft", "model": "anthropic/claude-sonnet-5",
                    "input_tokens": 4000, "output_tokens": 1500, "cost_usd": 0.023},
               ]}


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
    # cumulative cost/usage rides along in stage_outputs for the quote's own
    # detail view, and a dashboard event was recorded for this generation
    assert row["stage_outputs"]["generation_summary"]["total_cost_usd"] == pytest.approx(0.024)
    assert "generation_duration_seconds" in row["stage_outputs"]
    assert len(api.fake_store.generation_events) == 1
    event = api.fake_store.generation_events[0]
    assert event["quote_id"] == row["id"] and event["trigger"] == "initial"


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


def test_revise_records_only_this_events_own_incremental_stats(api, monkeypatch):
    """generation_stats accumulates across the whole thread (v1 + this
    revision), so the CUMULATIVE total in stage_outputs must include both,
    but the dashboard event for THIS revision must record only its own new
    entries, not v1's already-recorded cost too (that would double-count
    across the two quote_generation_events rows)."""
    _set_graph(monkeypatch, DRAFT_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})

    revise_new_stats = [
        {"stage": "codes", "model": "anthropic/claude-haiku-4.5",
         "input_tokens": 300, "output_tokens": 50, "cost_usd": 0.0005},
        {"stage": "draft", "model": "anthropic/claude-sonnet-5",
         "input_tokens": 4200, "output_tokens": 1600, "cost_usd": 0.025},
    ]
    revise_state = {**DRAFT_STATE, "draft": "# Quote v2",
                    "generation_stats": DRAFT_STATE["generation_stats"] + revise_new_stats}
    g = _set_graph(monkeypatch, revise_state)
    # get_state() (read before invoke()) reflects the checkpointer's state
    # BEFORE this revision -- i.e. just v1's 2 entries; invoke() returns the
    # fuller post-revision state including the 2 new ones.
    g.get_state_override = {"generation_stats": DRAFT_STATE["generation_stats"]}

    row = api.post("/quotes/1/revise", json={"feedback": "drop the sauna"}).json()
    assert row["stage_outputs"]["generation_summary"]["total_cost_usd"] == pytest.approx(0.0495)

    events = api.fake_store.generation_events
    assert len(events) == 2  # v1's initial event, plus this revision's
    revise_event = events[-1]
    assert revise_event["trigger"] == "revise"
    assert revise_event["llm_calls"] == 2  # only this revision's own 2 calls
    assert revise_event["total_cost_usd"] == pytest.approx(0.0255)


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
        return {"draft": draft_md, "routing_packet": None,
                "generation_stats": [{"stage": "draft", "model": "anthropic/claude-sonnet-5",
                                      "input_tokens": 4000, "output_tokens": 1200,
                                      "cost_usd": 0.02}]}
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
    # cumulative cost = v1's 0.024 (from DRAFT_STATE via UNPRICED_STATE) +
    # this draft_node call's own 0.02; the dashboard event records only the
    # latter (this event's own incremental contribution)
    assert row["stage_outputs"]["generation_summary"]["total_cost_usd"] == pytest.approx(0.044)
    events = api.fake_store.generation_events
    assert len(events) == 2  # v1's initial event, plus this price override
    override_event = events[-1]
    assert override_event["trigger"] == "price_override"
    assert override_event["total_cost_usd"] == pytest.approx(0.02)
    assert override_event["llm_calls"] == 1
    audit = api.fake_store.price_overrides[0]
    assert audit == {"thread_id": "t1", "takeoff_line_ref": "t1",
                     "price_cad": 450, "note": "site visit quote",
                     "price_source_before": "unpriced",
                     "source_quote_id": 1, "result_quote_id": 2}


def test_override_price_accepts_zero(api, monkeypatch):
    """$0 is a legitimate estimator-entered price (e.g. an item already
    covered elsewhere, or a code-compliance line with no cost) -- confirmed
    live it was rejected outright (422) before reaching the handler at all,
    because the request schema required price_cad > 0 instead of >= 0."""
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    captured = {}
    _fake_draft_node(monkeypatch, captured)
    out = api.post("/quotes/1/lines/t1/price",
                   json={"price_cad": 0, "note": "no charge, covered elsewhere"})
    assert out.status_code == 200
    overridden = captured["price_resolution"][0]
    assert overridden["price_source"] == "estimator_override"
    assert overridden["extended_quoted_cad"] == 0


def test_override_price_rejects_negative(api, monkeypatch):
    _set_graph(monkeypatch, UNPRICED_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    out = api.post("/quotes/1/lines/t1/price", json={"price_cad": -10})
    assert out.status_code == 422


def test_second_price_override_does_not_undercount_first_overrides_cost(api, monkeypatch):
    """Confirmed live: two price overrides on the same thread previously
    silently dropped the first override's own cost from the second's
    cumulative total, because override endpoints call draft_node directly
    (bypassing graph.invoke) and never told the checkpointer about their
    own new generation_stats entries -- so the second override's
    _load_active_quote_state read stale prior state missing the first
    one's contribution. _sync_generation_stats_to_checkpointer fixes this;
    this test reproduces the exact two-in-a-row scenario."""
    two_unpriced_state = {**DRAFT_STATE,
                          "price_resolution": [
                              {"category": "plumbing", "quantity": 1, "unit": "ea",
                               "takeoff_line_ref": "t1", "price_source": "unpriced"},
                              {"category": "electrical", "quantity": 1, "unit": "ea",
                               "takeoff_line_ref": "t2", "price_source": "unpriced"},
                          ]}
    g = _set_graph(monkeypatch, two_unpriced_state)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})

    captured = {}
    _fake_draft_node(monkeypatch, captured)
    first = api.post("/quotes/1/lines/t1/price", json={"price_cad": 100}).json()
    # v1's 0.024 (DRAFT_STATE) + this call's own 0.02
    assert first["stage_outputs"]["generation_summary"]["total_cost_usd"] == pytest.approx(0.044)
    # the checkpointer was told about this call's own new entry
    assert g.update_state_calls == [{"generation_stats": [
        {"stage": "draft", "model": "anthropic/claude-sonnet-5",
         "input_tokens": 4000, "output_tokens": 1200, "cost_usd": 0.02}]}]

    second = api.post(f"/quotes/{first['id']}/lines/t2/price", json={"price_cad": 200}).json()
    # Must be 0.024 + 0.02 (first override) + 0.02 (second override) = 0.064,
    # NOT 0.024 + 0.02 = 0.044 (which is what it would wrongly be if the
    # second override's prior-state read missed the first override's cost).
    assert second["stage_outputs"]["generation_summary"]["total_cost_usd"] == pytest.approx(0.064)


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
    """Only lines _needs_price() considers priceable are eligible -- a row
    with a real price_sheet result is not."""
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


TAVILY_NO_PRICE_STATE = {**DRAFT_STATE,
                         "price_resolution": [
                             {"category": "plumbing", "quantity": 1, "unit": "ea",
                              "takeoff_line_ref": "t1", "price_source": "unpriced",
                              "note": "no material or labor key on this line — "
                                      "estimator to price"},
                             {"category": "kitchen", "quantity": 1, "unit": "lump_sum",
                              "takeoff_line_ref": "t2", "price_source": "tavily",
                              "answer": "cabinetry typically costs around $356",
                              "note": None},
                         ]}


def test_override_price_accepts_tavily_row_with_no_usable_price(api, monkeypatch):
    """price_source "tavily" with no extended_quoted_cad (web search returned
    narrative text only, no parseable number) is just as unpriced as
    price_source "unpriced" -- seen live on quote #24's cabinetry line."""
    _set_graph(monkeypatch, TAVILY_NO_PRICE_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    captured = {}
    _fake_draft_node(monkeypatch, captured)
    out = api.post("/quotes/1/lines/t2/price", json={"price_cad": 900})
    assert out.status_code == 200
    priced = next(r for r in captured["price_resolution"]
                  if r["takeoff_line_ref"] == "t2")
    assert priced["price_source"] == "estimator_override"
    assert priced["extended_quoted_cad"] == 900


def test_batch_override_prices_multiple_lines_in_one_version(api, monkeypatch):
    _set_graph(monkeypatch, TAVILY_NO_PRICE_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    captured = {}
    _fake_draft_node(monkeypatch, captured)
    out = api.post("/quotes/1/lines/price", json={"overrides": [
        {"takeoff_line_ref": "t1", "price_cad": 250, "note": "site visit"},
        {"takeoff_line_ref": "t2", "price_cad": 900},
    ]})
    assert out.status_code == 200
    row = out.json()
    assert row["version"] == 2  # exactly one new version for both lines

    resolved = {r["takeoff_line_ref"]: r for r in captured["price_resolution"]}
    assert resolved["t1"]["price_source"] == "estimator_override"
    assert resolved["t1"]["extended_quoted_cad"] == 250
    assert resolved["t2"]["price_source"] == "estimator_override"
    assert resolved["t2"]["extended_quoted_cad"] == 900

    # one audit row per line, both pointing at the same new version
    assert len(api.fake_store.price_overrides) == 2
    assert {o["takeoff_line_ref"] for o in api.fake_store.price_overrides} == {"t1", "t2"}
    assert all(o["result_quote_id"] == row["id"] for o in api.fake_store.price_overrides)


def test_batch_override_all_or_nothing_on_bad_line(api, monkeypatch):
    """One invalid ref in the batch rejects the whole request -- no version
    is created, not even for the valid lines."""
    _set_graph(monkeypatch, TAVILY_NO_PRICE_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    out = api.post("/quotes/1/lines/price", json={"overrides": [
        {"takeoff_line_ref": "t1", "price_cad": 250},
        {"takeoff_line_ref": "does-not-exist", "price_cad": 900},
    ]})
    assert out.status_code == 404
    assert api.get("/quotes/1").json()["status"] == "pending_review"
    assert api.get("/quotes").json() == [api.get("/quotes/1").json()]
    assert api.fake_store.price_overrides == []


def test_batch_override_rejects_duplicate_refs(api, monkeypatch):
    _set_graph(monkeypatch, TAVILY_NO_PRICE_STATE)
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})
    out = api.post("/quotes/1/lines/price", json={"overrides": [
        {"takeoff_line_ref": "t1", "price_cad": 250},
        {"takeoff_line_ref": "t1", "price_cad": 300},
    ]})
    assert out.status_code == 400


def test_generation_stats_endpoint_aggregates_and_lists_recent(api, monkeypatch):
    """GET /quotes/generation-stats is the dashboard widget's one call --
    backed by quote_generation_events, not LangSmith (see main.py's
    generation_stats route docstring for why)."""
    # Empty prior state on both calls -- a brand-new thread, so each event's
    # own incremental slice is its FULL generation_stats list (the realistic
    # "nothing existed on this thread before" case).
    g1 = _set_graph(monkeypatch, DRAFT_STATE)
    g1.get_state_override = {}
    api.post("/chat", json={"thread_id": "t1", "message": "spec"})

    revise_new_stats = [{"stage": "draft", "model": "anthropic/claude-sonnet-5",
                         "input_tokens": 3000, "output_tokens": 900, "cost_usd": 0.018}]
    g2 = _set_graph(monkeypatch, {**DRAFT_STATE, "draft": "# Quote v2",
                                 "generation_stats": DRAFT_STATE["generation_stats"] + revise_new_stats})
    g2.get_state_override = {"generation_stats": DRAFT_STATE["generation_stats"]}
    api.post("/quotes/1/revise", json={"feedback": "n/a"})

    out = api.get("/quotes/generation-stats")
    assert out.status_code == 200
    data = out.json()
    assert data["totals"]["count"] == 2
    assert data["totals"]["total_cost_usd"] == pytest.approx(0.042)  # 0.024 (initial) + 0.018 (revise)
    assert len(data["recent"]) == 2
    assert {e["trigger"] for e in data["recent"]} == {"initial", "revise"}
