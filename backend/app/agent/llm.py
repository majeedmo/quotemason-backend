"""Chat model factories — OpenRouter for intake/drafting (CLAUDE.md model
table), with the brief's fallback routing via OpenRouter's `models` array."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_FALLBACKS = {  # brief, Task 2 model table
    "intake": ["google/gemini-2.5-flash"],
    "drafting": ["openai/gpt-5.1"],
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


def drafting_model() -> ChatOpenAI:
    return _chat(settings.drafting_model, "drafting", temperature=0.3)
