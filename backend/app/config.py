import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CORPUS_DIR = REPO_DIR / "corpus"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # The contractor this deployment serves. One process = one contractor;
    # a second contractor is a config + data exercise (own guidelines/quotes
    # ingested under their contractor_id), never a code change.
    contractor_id: str = "company-a"
    contractor_name: str = "Company A"          # internal name (guideline vocabulary)
    brand_name: str = "Maplewood Renovations"   # client-facing brand
    zoning_jurisdiction: str = "cambridge"
    price_staleness_days: int = 90

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "estimator_corpus"
    qdrant_local_path: Path = BACKEND_DIR / "qdrant_local"

    # Retriever the agent uses: "hybrid" (dense + BM25 + RRF, Task 6) or "dense".
    # The eval harness selects retrievers explicitly by name, independent of this.
    retriever: str = "hybrid"

    openrouter_api_key: str = ""
    drafting_model: str = "anthropic/claude-sonnet-5"
    intake_model: str = "anthropic/claude-haiku-4.5"
    judge_model: str = "openai/gpt-5.1"

    tavily_api_key: str = ""
    upstash_redis_url: str = ""
    database_url: str = ""

    langsmith_api_key: str = ""
    langsmith_project: str = "quotemason"
    langsmith_tracing: bool = True  # trace whenever the API key is set

    # Comma-separated browser origins allowed to call the API (Next.js dev
    # server by default — 3001 included because Next falls back to it when
    # 3000 is already taken; add the Vercel URL for the deploy).
    cors_origins: str = "http://localhost:3000,http://localhost:3001"


settings = Settings()

# LangChain/LangGraph read tracing config from the process environment, not
# from Settings — and pydantic only parses .env, it doesn't export it. Without
# this, LangGraph runs are never traced (the §6 routing-event tags become
# no-ops) even with a key in .env. Both var spellings for langsmith-sdk
# compatibility; setdefault so real env vars (e.g. on Render) always win.
if settings.langsmith_api_key and settings.langsmith_tracing:
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
