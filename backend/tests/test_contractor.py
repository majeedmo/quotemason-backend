"""Contractor identity threading — no network, no API keys.

The refactor's contract: contractor identity is config + metadata, never a
hardcoded string. These tests pin the three seams: prompt templates format
from settings, loaders stamp contractor metadata on contractor-owned docs
only, and chunk-text prefixes derive from that metadata.
"""

from app.agent import prompts
from app.contractor import get_contractor
from app.ingestion.chunking import chunk_doc
from app.ingestion.loaders import CorpusDoc, load_guidelines, load_obc


def test_profile_resolves_from_settings():
    p = get_contractor()
    assert p.id == "company-a"
    assert p.guideline_doc.exists()


def test_intake_system_formats_with_brand_not_internal_name():
    text = prompts.intake_system()
    # Full-template format() must succeed despite the {{ }} JSON braces, and
    # the brand must come from settings.
    assert "Maplewood Renovations" in text
    assert '"action": "ask" | "complete" | "hard_route"' in text


def test_draft_narrative_system_formats_with_contractor_name():
    assert "Company A" in prompts.draft_narrative_system()


def test_loaders_stamp_contractor_only_on_owned_docs():
    g = load_guidelines()
    assert g and all(d.metadata["contractor_id"] == "company-a" for d in g)
    assert all(d.metadata["contractor_name"] == "Company A" for d in g)
    obc = load_obc()
    assert obc and all("contractor_id" not in d.metadata for d in obc)


def test_guideline_chunk_prefix_uses_metadata_contractor_name():
    doc = CorpusDoc(
        source_file="corpus/guidelines/x.md",
        text="## 4. Materials\n" + "waste factors and rules of thumb " * 5,
        metadata={"doc_type": "builder_guideline", "contractor_name": "Company B",
                  "source_version": "v1"})
    chunks = chunk_doc(doc)
    assert chunks and chunks[0].text.startswith("[Company B builder guidelines")
