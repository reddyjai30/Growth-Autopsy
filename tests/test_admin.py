from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from growth_autopsy import admin
from growth_autopsy.api import app, get_store
from growth_autopsy.config import Settings
from growth_autopsy.store import WorkflowStore


def test_database_browser_is_allowlisted_searchable_and_read_only(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    store.set_setting("newsletter_status", "ready for review")

    overview = admin.database_overview(store)
    settings_table = next(table for table in overview["tables"] if table["key"] == "settings")
    records = admin.database_rows(store, "settings", search="newsletter", limit=10)
    record = admin.database_record(store, "settings", records["rows"][0]["__rowid__"])

    assert overview["engine"] == "SQLite"
    assert overview["read_only"] is True
    assert settings_table["count"] == 1
    assert records["total"] == 1
    assert record["record"]["value"] == "ready for review"
    with pytest.raises(ValueError, match="Unknown admin database table"):
        admin.database_rows(store, "sqlite_master")


def test_config_overview_never_returns_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'GA_AI_API_KEY="stored-secret-sentinel"\nGA_AI_MODEL="gpt-example"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(admin, "env_file_path", lambda: env_file)
    settings = Settings(
        ai_api_key="effective-secret-sentinel",
        ai_model="gpt-example",
        linkedin_client_id="linkedin-client-id",
        linkedin_client_secret="linkedin-secret-sentinel",
        google_token_file=tmp_path / "google-token.json",
        linkedin_token_file=tmp_path / "linkedin-token.json",
    ).resolve_paths(tmp_path)

    payload = admin.config_overview(settings)
    encoded = json.dumps(payload)
    ai_secret = next(field for field in payload["fields"] if field["key"] == "GA_AI_API_KEY")
    ai_model = next(field for field in payload["fields"] if field["key"] == "GA_AI_MODEL")

    assert ai_secret["configured"] is True
    assert ai_secret["value"] == ""
    assert ai_model["value"] == "gpt-example"
    assert "stored-secret-sentinel" not in encoded
    assert "effective-secret-sentinel" not in encoded
    assert "linkedin-secret-sentinel" not in encoded


def test_config_updates_are_atomic_private_and_require_explicit_secret_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# Keep this comment\nGA_AI_API_KEY="existing-secret"\nCUSTOM_VALUE=untouched\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(admin, "env_file_path", lambda: env_file)

    updated = admin.update_env_file(
        {
            "GA_AI_API_KEY": "",
            "GA_AI_MODEL": "gpt-example",
            "GA_ENABLE_BACKGROUND_SYNC": "false",
        }
    )
    first_content = env_file.read_text(encoding="utf-8")

    assert set(updated) == {"GA_AI_MODEL", "GA_ENABLE_BACKGROUND_SYNC"}
    assert "existing-secret" in first_content
    assert "# Keep this comment" in first_content
    assert "CUSTOM_VALUE=untouched" in first_content
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    admin.update_env_file({}, clear_secrets=["GA_AI_API_KEY"])
    assert 'GA_AI_API_KEY=""' in env_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Only secret fields"):
        admin.update_env_file({}, clear_secrets=["GA_AI_MODEL"])


def test_meta_ad_library_country_is_normalized_and_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(admin, "env_file_path", lambda: env_file)

    admin.update_env_file({"GA_META_AD_LIBRARY_COUNTRY": "gb"})

    assert 'GA_META_AD_LIBRARY_COUNTRY="GB"' in env_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="ALL or a two-letter country code"):
        admin.update_env_file({"GA_META_AD_LIBRARY_COUNTRY": "United Kingdom"})


def test_google_oauth_upload_accepts_only_desktop_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin, "env_file_path", lambda: tmp_path / ".env")
    document = {
        "installed": {
            "client_id": "client-id.apps.googleusercontent.com",
            "client_secret": "oauth-secret-sentinel",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    destination = admin.save_google_oauth_client(document)

    assert destination == tmp_path / "secrets" / "google-oauth-client.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == document
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="Desktop app JSON"):
        admin.save_google_oauth_client({"web": document["installed"]})


@pytest.mark.asyncio
async def test_admin_api_rejects_remote_clients_and_accepts_loopback(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    app.dependency_overrides[get_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.8", 1234)),
            base_url="http://example.test",
        ) as remote_client:
            rejected = await remote_client.get("/internal/admin/database")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://rebound.example",
        ) as rebound_client:
            rebound_rejected = await rebound_client.get("/internal/admin/database")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1",
        ) as local_client:
            accepted = await local_client.get("/internal/admin/database")
            admin_page = await local_client.get("/admin/")
    finally:
        app.dependency_overrides.pop(get_store, None)

    assert rejected.status_code == 403
    assert rebound_rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["read_only"] is True
    assert accepted.headers["cache-control"] == "private, no-store"
    assert admin_page.status_code == 200
    assert "Workspace admin" in admin_page.text


def test_admin_static_ui_contains_database_config_and_oauth_controls() -> None:
    dashboard = Path(__file__).parents[1] / "src/growth_autopsy/dashboard"
    admin_html = (dashboard / "admin/index.html").read_text(encoding="utf-8")
    admin_javascript = (dashboard / "admin/admin.js").read_text(encoding="utf-8")
    pipeline_html = (dashboard / "index.html").read_text(encoding="utf-8")

    assert "Workspace admin" in admin_html
    assert "Database read-only" in admin_html
    assert "Upload Google JSON" in admin_html
    assert "Connect LinkedIn" in admin_html
    assert "oauth.connect_url" in admin_javascript
    assert "clear_secrets" in admin_javascript
    assert "/internal/admin/database" in admin_javascript
    assert 'href="/admin/"' in pipeline_html
