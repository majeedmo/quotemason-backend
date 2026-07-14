from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CORPUS_DIR = REPO_DIR / "corpus"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "estimator_corpus"
    qdrant_local_path: Path = BACKEND_DIR / "qdrant_local"

    openrouter_api_key: str = ""
    drafting_model: str = "anthropic/claude-sonnet-5"
    intake_model: str = "anthropic/claude-haiku-4.5"
    judge_model: str = "openai/gpt-5.1"

    tavily_api_key: str = ""
    upstash_redis_url: str = ""
    database_url: str = ""

    langsmith_api_key: str = ""
    langsmith_project: str = "quotemason"

    # Comma-separated browser origins allowed to call the API (Next.js dev
    # server by default — 3001 included because Next falls back to it when
    # 3000 is already taken; add the Vercel URL for the deploy).
    cors_origins: str = "http://localhost:3000,http://localhost:3001"


settings = Settings()
