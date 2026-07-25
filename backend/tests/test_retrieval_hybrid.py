"""Unit tests for the hybrid retriever — no network, no API keys.

The dense half is a fake (records its calls and returns canned hits); the BM25
half is real, built over an injected mini-corpus. Filler chunks keep the
discriminating query terms rare, so BM25 IDF stays positive — the same regime
as the real 730-chunk corpus (in a 1-2 doc corpus every term is ubiquitous and
IDF goes negative, which is not how production behaves). Live end-to-end scoring
happens via app.evals.run_retrieval_eval --retriever hybrid.
"""

from types import SimpleNamespace

from app.retrieval.hybrid import HybridRetriever, _match_meta, _tokenize
from app.retrieval.retriever import RetrievedChunk


def _chunk(text, **meta):
    return SimpleNamespace(text=text, metadata=meta)


# Diverse filler so target terms stay rare (positive IDF), mirroring production.
_FILLER = [
    _chunk("[OBC 9.10.9 — Fire Separations] assembly rating requirements",
           doc_type="building_code", jurisdiction="ontario", section_number="9.10.9"),
    _chunk("[OBC 9.31 — Plumbing Facilities] fixture counts per suite",
           doc_type="building_code", jurisdiction="ontario", section_number="9.31"),
    _chunk("[OBC 9.32.3.9 — Carbon Monoxide Alarms] fuel-burning appliance",
           doc_type="building_code", jurisdiction="ontario", section_number="9.32.3.9"),
    _chunk("[Cambridge Zoning 4.19 — ARUs] parking additional residential unit",
           doc_type="zoning_bylaw", jurisdiction="cambridge", section_number="4.19"),
    _chunk("[Company A guidelines — 5 Quoting Rules] HST deposit milestones",
           doc_type="builder_guideline", section_number="5",
           contractor_id="company-a"),
    _chunk("[Past project P07] flooring laminate rec room finished basement",
           doc_type="past_project_quote", project_code="P07", synthetic=False,
           contractor_id="company-a"),
]


class _FakeDense:
    """Stands in for CorpusRetriever: records filters, returns fixed dense hits."""
    def __init__(self, hits=None):
        self.calls = []
        self._hits = hits or []

    def search(self, query, *, k=5, must=None, must_not=None):
        self.calls.append(SimpleNamespace(query=query, k=k, must=must, must_not=must_not))
        return list(self._hits)


def _hybrid(extra, dense_hits=None):
    return HybridRetriever(dense=_FakeDense(hits=dense_hits or []),
                           chunks=_FILLER + extra)


def test_tokenizer_keeps_dotted_and_hyphenated_identifiers_whole():
    toks = _tokenize("Egress per OBC 9.9.10.1 and By-law 26-007")
    assert "9.9.10.1" in toks
    assert "26-007" in toks
    assert "9" not in toks  # not split into pieces


def test_match_meta_mirrors_qdrant_filter_semantics():
    m = {"doc_type": "past_project_quote", "city": "Oakville", "synthetic": True}
    # None constraints are ignored; must is AND
    assert _match_meta(m, {"doc_type": "past_project_quote", "city": None}, None)
    assert not _match_meta(m, {"doc_type": "building_code"}, None)
    # must_not excludes on match
    assert not _match_meta(m, None, {"synthetic": True})
    assert _match_meta({"synthetic": False}, None, {"synthetic": True})


def test_match_meta_list_value_is_membership_not_equality():
    """A list value means "any of these" -- used for leave-one-out eval
    retrieval (excluding a set of project codes)."""
    p19 = {"project_code": "P19"}
    other = {"project_code": "P07"}
    assert not _match_meta(p19, None, {"project_code": ["P19", "S01"]})
    assert _match_meta(other, None, {"project_code": ["P19", "S01"]})


def test_bm25_matches_exact_clause_number_dense_would_blur():
    target = _chunk(
        "[OBC 9.41 — Additional Requirements for Change of Use] second suite conversion",
        doc_type="building_code", jurisdiction="ontario", section_number="9.41")
    r = _hybrid([target])
    hits = r.search_building_code("requirements for change of use 9.41", k=3)
    assert hits[0].metadata["section_number"] == "9.41"


def test_helpers_pass_correct_filters_to_dense():
    dense = _FakeDense(hits=[])
    r = HybridRetriever(dense=dense, chunks=_FILLER)
    r.search_past_quotes("stairs", k=3, package_tier="SUPREME", include_synthetic=False)
    call = dense.calls[-1]
    assert call.must == {"doc_type": "past_project_quote",
                         "contractor_id": "company-a", "city": None,
                         "package_tier": "SUPREME", "scope": None}
    assert call.must_not == {"synthetic": True}


def test_bm25_honours_synthetic_exclusion():
    extra = [
        _chunk("[Past project S01 (SYNTHETIC)] sauna wine cellar theatre",
               doc_type="past_project_quote", project_code="S01", synthetic=True,
               contractor_id="company-a"),
        _chunk("[Past project P19] sauna wine cellar theatre",
               doc_type="past_project_quote", project_code="P19", synthetic=False,
               contractor_id="company-a"),
    ]
    r = _hybrid(extra)
    codes = [h.metadata["project_code"]
             for h in r.search_past_quotes("sauna wine cellar", k=5, include_synthetic=False)]
    assert "S01" not in codes


def test_bm25_honours_exclude_project_codes():
    """Leave-one-out for the quote-accuracy eval — excludes a project's own
    historical quote (and its synthetic twin) from BM25 hits too, not just
    the dense side."""
    extra = [
        _chunk("[Past project P19] finished basement superior tier",
               doc_type="past_project_quote", project_code="P19", synthetic=False,
               contractor_id="company-a"),
        _chunk("[Past project S01 (SYNTHETIC)] finished basement essential tier",
               doc_type="past_project_quote", project_code="S01", synthetic=True,
               contractor_id="company-a"),
        _chunk("[Past project P07] finished basement unrelated",
               doc_type="past_project_quote", project_code="P07", synthetic=False,
               contractor_id="company-a"),
    ]
    r = _hybrid(extra)
    codes = [h.metadata["project_code"] for h in r.search_past_quotes(
        "finished basement", k=5, exclude_project_codes=["P19", "S01"])]
    assert "P19" not in codes and "S01" not in codes
    assert "P07" in codes


def test_helpers_pass_exclude_project_codes_to_dense():
    dense = _FakeDense(hits=[])
    r = HybridRetriever(dense=dense, chunks=_FILLER)
    r.search_past_quotes("basement", k=3, exclude_project_codes=["P19", "S01"])
    call = dense.calls[-1]
    assert call.must_not == {"project_code": ["P19", "S01"]}


def test_rrf_rewards_a_chunk_found_by_both_retrievers():
    shared = _chunk("[OBC 9.9.10 — Egress] bedroom egress window openable sill",
                    doc_type="building_code", jurisdiction="ontario", section_number="9.9.10")
    dense_only = RetrievedChunk(text="dense only chunk unrelated", score=0.9,
                                metadata={"doc_type": "building_code",
                                          "jurisdiction": "ontario", "section_number": "9.7"})
    shared_dense = RetrievedChunk(text=shared.text, score=0.5, metadata=shared.metadata)
    r = _hybrid([shared], dense_hits=[dense_only, shared_dense])
    hits = r.search_building_code("bedroom egress window sill", k=3)
    # shared is rank-2 in dense but also the top BM25 hit -> RRF lifts it to #1
    assert hits[0].metadata["section_number"] == "9.9.10"
    assert hits[0].text == shared.text


def test_returned_objects_expose_working_citation():
    target = _chunk("[OBC 9.8.2.1 — Stair Width] minimum stair width dimensions",
                    doc_type="building_code", jurisdiction="ontario",
                    section_number="9.8.2.1", section_title="Stair Width",
                    source_version="2024")
    r = _hybrid([target])
    hit = r.search_building_code("stair width dimensions", k=2)[0]
    assert hit.citation == "OBC 9.8.2.1 — Stair Width | 2024"
