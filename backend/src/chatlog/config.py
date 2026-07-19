from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CHATLOG_",
        extra="ignore",
    )

    app_name: str = "Chat inference log API"
    database_url: str = Field(default="postgresql+asyncpg://chatlog:chatlog@localhost:5432/chatlog")
    sql_echo: bool = False
    cors_origins: str = "http://localhost:3000"
    groq_api_key: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
