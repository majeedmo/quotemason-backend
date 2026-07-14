"""Unit tests for the retrieval module — no network, no API keys.

Live end-to-end check happens via the smoke CLI (app.retrieval.smoke_test)
against the ingested local collection.
"""

from types import SimpleNamespace

from app.retrieval.retriever import CorpusRetriever, RetrievedChunk, _conditions


def test_conditions_drop_none_and_prefix_metadata():
    conds = _conditions({"doc_type": "past_project_quote", "city": None,
                         "package_tier": "ESSENTIAL"})
    assert [(c.key, c.match.value) for c in conds] == [
        ("metadata.doc_type", "past_project_quote"),
        ("metadata.package_tier", "ESSENTIAL"),
    ]


def test_citation_building_code():
    c = RetrievedChunk(text="", score=1.0, metadata={
        "doc_type": "building_code", "section_number": "9.9.10.1",
        "section_title": "Egress Windows or Doors for Bedrooms",
        "source_version": "2024 Building Code Compendium (O. Reg. 163/24)"})
    assert c.citation == ("OBC 9.9.10.1 — Egress Windows or Doors for Bedrooms"
                          " | 2024 Building Code Compendium (O. Reg. 163/24)")


def test_citation_synthetic_quote_is_marked():
    c = RetrievedChunk(text="", score=1.0, metadata={
        "doc_type": "past_project_quote", "project_code": "S01",
        "synthetic": True, "city": "Oakville", "package_tier": "ESSENTIAL",
        "section_title": "One Powder Room"})
    assert "S01 (SYNTHETIC)" in c.citation and "Oakville" in c.citation


class _FakeEmbeddings:
    def embed_query(self, q):
        return [0.0] * 1536


class _FakeClient:
    def __init__(self):
        self.calls = []

    def query_points(self, collection, query, limit, query_filter, with_payload):
        self.calls.append(SimpleNamespace(collection=collection, limit=limit,
                                          query_filter=query_filter))
        pt = SimpleNamespace(score=0.9, payload={
            "page_content": "[OBC 9.8.2.1 — Stair Width]\n...",
            "metadata": {"doc_type": "building_code"}})
        return SimpleNamespace(points=[pt])


def test_search_builds_filter_and_maps_payload():
    fake = _FakeClient()
    r = CorpusRetriever(client=fake, embeddings=_FakeEmbeddings())
    hits = r.search_past_quotes("stairs", k=3, package_tier="SUPREME",
                                include_synthetic=False)
    assert len(hits) == 1 and hits[0].text.startswith("[OBC 9.8.2.1")
    call = fake.calls[0]
    assert call.limit == 3
    must_keys = {c.key for c in call.query_filter.must}
    assert must_keys == {"metadata.doc_type", "metadata.package_tier"}
    assert [c.key for c in call.query_filter.must_not] == ["metadata.synthetic"]


def test_search_no_filters_passes_none():
    fake = _FakeClient()
    r = CorpusRetriever(client=fake, embeddings=_FakeEmbeddings())
    r.search("anything", k=2)
    assert fake.calls[0].query_filter is None
