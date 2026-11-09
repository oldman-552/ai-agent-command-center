from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://acc:acc_dev@localhost:5432/acc"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    # "bus": the API writes an outbox message and workers run executions (production).
    # "inline": run in the API process — unit tests and single-process debugging only.
    execution_dispatch: Literal["bus", "inline"] = "bus"

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    app_encryption_key: str

    admin_username: str = "admin"
    admin_password: str = ""

    workspaces_dir: str = "/data/workspaces"
    sandbox_cpu_limit: float = 1.0
    sandbox_memory_limit: str = "1g"
    sandbox_timeout_seconds: int = 900

    cookie_secure: bool = False  # set True behind HTTPS

    @field_validator("admin_password")
    @classmethod
    def _admin_pw(cls, v: str) -> str:
        if v and len(v) < 12:
            raise ValueError("ADMIN_PASSWORD must be at least 12 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
