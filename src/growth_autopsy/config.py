from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from ``GA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GA_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "development"
    database_path: Path = Path("data/growth_autopsy.db")
    shared_workdir: Path = Path("data")

    google_calendar_id: str = "primary"
    google_token_file: Path = Path("secrets/google-token.json")
    calendar_title_prefix: str = "[GROWTH AUTOPSY]"
    diksha_email: str = ""
    calendar_lookahead_days: int = Field(default=30, ge=1, le=365)
    calendar_lookback_hours: int = Field(default=24, ge=0, le=168)
    precall_start_minutes: int = Field(default=60, ge=30, le=240)
    precall_delivery_minutes: int = Field(default=30, ge=5, le=120)

    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_delivery_target: str = "local"

    fathom_webhook_secret: str = ""
    fathom_api_key: str = ""
    fathom_match_window_minutes: int = Field(default=20, ge=5, le=120)
    max_fathom_webhook_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=25 * 1024 * 1024,
    )

    internal_api_key: str = ""

    def resolve_paths(self, base_dir: Path | None = None) -> "Settings":
        base = (base_dir or Path.cwd()).resolve()
        for field_name in ("database_path", "shared_workdir", "google_token_file"):
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, (base / value).resolve())
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().resolve_paths()

