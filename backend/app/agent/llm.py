"""Chat model factories — OpenRouter for intake/drafting (CLAUDE.md model
table), with the brief's fallback routing via OpenRouter's `models` array."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_FALLBACKS = {  # brief, Task 2 model table
    "intake": ["google/gemini-2.5-flash"],
    "codes": ["google/gemini-2.5-flash"],
    "drafting": ["openai/gpt-5.1"],
    "takeoff": ["google/gemini-2.5-flash"],
}


def _chat(model: str, role: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        extra_body={"models": _FALLBACKS[role]},
    )


def intake_model() -> ChatOpenAI:
    return _chat(settings.intake_model, "intake", temperature=0.2)


def codes_model() -> ChatOpenAI:
    """Stage-1 codes checklist (tool-calling over the regulatory service).
    Same tier as intake but a DISTINCT factory: tests trap intake_model to
    prove revisions skip intake, and the codes stage must not trip it."""
    return _chat(settings.intake_model, "codes", temperature=0.1)


def drafting_model() -> ChatOpenAI:
    """Cost experiment 2026-07-25: draft stage on GLM-5.2 instead of
    settings.drafting_model (claude-sonnet-5) -- intake/codes stay on Haiku,
    takeoff also on GLM-5.2 (see takeoff_model()). Validate against the
    quote-accuracy eval before trusting this (see docs/capstone-progress.md)."""
    return _chat("z-ai/glm-5.2", "drafting", temperature=0.3)


def takeoff_model() -> ChatOpenAI:
    """Cost experiment 2026-07-25: GLM-5.2 on takeoff too (matching the
    drafting_model() experiment above) -- intake/codes stay on Haiku.
    Validated against the quote-accuracy eval before trusting it (see
    docs/capstone-progress.md)."""
    return _chat("z-ai/glm-5.2", "takeoff", temperature=0.1)
