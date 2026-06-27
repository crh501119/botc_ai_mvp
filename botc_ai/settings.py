from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    ai_dialogue_model: str = Field(default="gpt-5.4-mini", alias="AI_DIALOGUE_MODEL")
    ai_decision_model: str = Field(default="gpt-5.4-nano", alias="AI_DECISION_MODEL")
    openai_store: bool = Field(default=False, alias="OPENAI_STORE")
    mock_ai: bool = Field(default=False, alias="MOCK_AI")
    game_budget_usd: float = Field(default=1.0, alias="GAME_BUDGET_USD")
    dev_reveal: bool = Field(default=False, alias="DEV_REVEAL")
    database_url: str = Field(default="sqlite:///./botc_ai.sqlite3", alias="DATABASE_URL")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
