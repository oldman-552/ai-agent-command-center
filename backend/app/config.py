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
    circuit_failure_threshold: int = 5      # consecutive failures that open an agent/provider circuit
    circuit_cooldown_seconds: int = 300     # open → one trial request (half-open) after this long
    shutdown_grace_seconds: int = 30
    supervisor_interval_seconds: int = 30
    supervisor_stall_seconds: int = 300        # running with no event for this long = stalled
    supervisor_runaway_cost_usd: float = 5.0   # one execution costing more than this = runaway
    supervisor_failure_window_minutes: int = 60
    supervisor_repeated_failures: int = 3
    supervisor_retry_threshold: int = 3
    supervisor_denials_threshold: int = 3
    # reversible actions the supervisor may take on its own (spec Phase 5); remove one to make it advisory only
    supervisor_actions: str = "stop_runaway,resume_stalled,isolate_agent,retry_task,reduce_concurrency"        # worker: let in-flight work finish before checkpoint-and-handoff
    redis_url: str = "redis://localhost:6379/0"  # live updates (pub/sub) and service heartbeats
    browser_bridge_url: str = "http://127.0.0.1:8100"   # the Browser Bridge service (python -m app.browser.server)
    platform_internal_url: str = "http://127.0.0.1:8000"  # where the bridge finds the platform test page
    browser_profiles_dir: str = "/data/browser-profiles"  # dedicated, platform-owned profiles only (ADR-025)
    browser_headless: bool = True
    browser_max_sessions: int = 4
    browser_generation_timeout_seconds: int = 300
    skills_dir: str = "/data/skills"  # local skill folders that can be inspected and installed (no remote fetch)
    sandbox_image: str = "acc/sandbox:python"
    cli_sandbox_image: str = "acc/cli:latest"  # docker/cli/Dockerfile: the coding CLIs, built by the owner (EE-02)  # built from docker/sandbox/Dockerfile

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
