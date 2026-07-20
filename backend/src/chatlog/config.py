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
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_stream: str = "inference_logs"
    redis_dlq_stream: str = "inference_logs_dlq"
    redis_consumer_group: str = "inference_logs_writers"
    redis_consumer_name: str = "consumer-1"
    redis_max_delivery_attempts: int = Field(default=5, ge=1)
    sql_echo: bool = False
    cors_origins: str = "http://localhost:3000"
    groq_api_key: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
