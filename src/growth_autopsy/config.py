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

    # Production operator access. These values are intentionally supplied by
    # the hosting provider rather than written through the admin console.
    app_username: str = ""
    app_password: str = ""
    session_secret: str = ""
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    managed_configuration: bool = False

    google_calendar_id: str = "primary"
    google_token_file: Path = Path("secrets/google-token.json")
    calendar_title_prefix: str = "[GROWTH AUTOPSY]"
    diksha_email: str = ""
    calendar_lookahead_days: int = Field(default=30, ge=1, le=365)
    calendar_lookback_hours: int = Field(default=24, ge=0, le=168)
    precall_start_minutes: int = Field(default=60, ge=30, le=240)
    precall_delivery_minutes: int = Field(default=30, ge=5, le=120)

    precall_research_backend: str = "local_free"
    precall_max_pages: int = Field(default=12, ge=1, le=30)
    precall_max_concurrency: int = Field(default=4, ge=1, le=10)
    precall_http_timeout_seconds: int = Field(default=20, ge=5, le=90)
    precall_max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=65536, le=10485760)
    precall_search_enabled: bool = True
    precall_search_results_per_query: int = Field(default=5, ge=1, le=10)
    precall_search_timeout_seconds: int = Field(default=20, ge=5, le=60)
    precall_pagespeed_enabled: bool = True
    pagespeed_api_key: str = ""
    precall_pagespeed_timeout_seconds: int = Field(default=60, ge=10, le=120)
    precall_local_lighthouse_enabled: bool = True
    lighthouse_executable: Path = Path("node_modules/.bin/lighthouse")
    precall_local_lighthouse_timeout_seconds: int = Field(default=90, ge=30, le=180)
    precall_collection_stale_minutes: int = Field(default=20, ge=5, le=120)
    precall_max_parallel_appointments: int = Field(default=2, ge=1, le=5)
    playwright_enabled: bool = True
    playwright_timeout_seconds: int = Field(default=30, ge=5, le=90)
    playwright_settle_milliseconds: int = Field(default=1500, ge=0, le=10000)

    # Semrush has no unrestricted free SEO/traffic API. These settings activate
    # the official paid API only when a licensed key is supplied.
    semrush_api_key: str = ""
    semrush_database: str = "us"
    semrush_country: str = "us"
    semrush_timeout_seconds: int = Field(default=20, ge=5, le=60)
    semrush_mcp_enabled: bool = False
    semrush_mcp_url: str = "https://mcp.semrush.com/v2/mcp"
    semrush_mcp_max_reports: int = Field(default=3, ge=1, le=5)

    # Direct OpenAI-compatible model endpoint used for evidence synthesis.
    # Hermes is deliberately not part of the Phase 1 runtime.
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = Field(default=120, ge=10, le=300)
    ai_max_output_tokens: int = Field(default=6000, ge=1000, le=20000)

    # Legacy Phase 2 fields remain temporarily so the existing Fathom routes
    # can be migrated without discarding the already-built post-call work.
    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_model: str = ""
    hermes_provider: str = ""
    hermes_delivery_target: str = "local"
    hermes_cron_output_dir: Path = Field(
        default_factory=lambda: Path.home() / ".hermes" / "cron" / "output"
    )

    enable_background_sync: bool = True
    background_sync_interval_seconds: int = Field(default=60, ge=15, le=3600)

    fathom_webhook_secret: str = ""
    fathom_api_key: str = ""
    fathom_match_window_minutes: int = Field(default=20, ge=5, le=120)
    max_fathom_webhook_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=25 * 1024 * 1024,
    )

    notion_api_key: str = ""
    notion_parent_page_id: str = ""
    notion_api_version: str = "2026-03-11"
    notion_publish_after_approval: bool = True

    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = (
        "http://localhost:8787/internal/linkedin/oauth/callback"
    )
    linkedin_token_file: Path = Path("secrets/linkedin-token.json")
    linkedin_api_version: str = Field(default="202607", pattern=r"^\d{6}$")
    linkedin_publish_after_notion: bool = False

    def resolve_paths(self, base_dir: Path | None = None) -> "Settings":
        base = (base_dir or Path.cwd()).resolve()
        for field_name in (
            "database_path",
            "shared_workdir",
            "google_token_file",
            "linkedin_token_file",
            "hermes_cron_output_dir",
            "lighthouse_executable",
        ):
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, (base / value).resolve())
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().resolve_paths()
