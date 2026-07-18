"""Shared regulatory service tests — no network, no API keys.

The module is the MCP-shaped boundary between agents and the public
regulatory corpus: stateless, JSON-serializable in/out, no contractor
coupling. applicable_codes() carries the deterministic always-check logic
that used to live inline in retrieve_node — these tests pin that port.
"""

from types import SimpleNamespace

from app.tools import regulatory


class _FakeRetriever:
    def __init__(self):
        self.code_queries = []
        self.zoning_calls = []

    def _hit(self, cit, **meta):
        return SimpleNamespace(citation=cit, text="...", metadata=meta)

    def search_building_code(self, q, k=5):
        self.code_queries.append(q)
        return [self._hit(f"OBC for: {q}", section_number="9.x",
                          doc_type="building_code")]

    def search_zoning(self, q, k=5, jurisdiction=None):
        self.zoning_calls.append(jurisdiction)
        return [self._hit("By-law 26-007 §4.19", section_number="4.19",
                          doc_type="zoning_bylaw")]


def _fake(monkeypatch):
    fake = _FakeRetriever()
    monkeypatch.setattr(regulatory, "get_retriever", lambda: fake)
    return fake


def test_baseline_scope_checks_ceiling_height_only(monkeypatch):
    fake = _fake(monkeypatch)
    out = regulatory.applicable_codes({"scope": "finished basement"})
    assert fake.code_queries == ["minimum ceiling height basement rooms"]
    assert "zoning_bylaw" not in out
    row = out["building_code"][0]
    assert set(row) == {"citation", "text", "section_number", "doc_type"}


def test_accessory_with_bedrooms_adds_egress_suite_and_zoning(monkeypatch):
    fake = _fake(monkeypatch)
    out = regulatory.applicable_codes(
        {"scope": "legal accessory unit", "bedrooms_egress": "1 bedroom, no egress"})
    qs = " | ".join(fake.code_queries)
    assert "egress" in qs and "fire separation" in qs and "change of use" in qs
    assert out["zoning_bylaw"][0]["section_number"] == "4.19"
    # jurisdiction defaulted from settings, not hardcoded at the call site
    assert fake.zoning_calls == [regulatory.settings.zoning_jurisdiction]


def test_pack_dedups_by_citation(monkeypatch):
    fake = _fake(monkeypatch)
    out = regulatory.applicable_codes(
        {"scope": "basement", "bedrooms_egress": "yes"})
    # fake returns one hit per query with distinct citations -> 2 rows, and a
    # duplicate citation would have been collapsed
    assert len(out["building_code"]) == len(fake.code_queries)
    rows = regulatory._pack([fake._hit("same"), fake._hit("same"), fake._hit("other")])
    assert [r["citation"] for r in rows] == ["same", "other"]


def test_langchain_tools_wrap_the_search_functions(monkeypatch):
    _fake(monkeypatch)
    names = {t.name for t in regulatory.REGULATORY_TOOLS}
    assert names == {"building_code_lookup", "zoning_lookup"}
    rows = regulatory.building_code_lookup.invoke({"query": "egress window"})
    assert rows and rows[0]["citation"].startswith("OBC for:")
    rows = regulatory.zoning_lookup.invoke({"query": "ARU parking"})
    assert rows and rows[0]["doc_type"] == "zoning_bylaw"
