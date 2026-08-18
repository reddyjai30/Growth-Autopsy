from __future__ import annotations

from urllib.parse import urlencode

import httpx
import pytest

from growth_autopsy import api as api_module
from growth_autopsy.api import app, get_store
from growth_autopsy.config import Settings
from growth_autopsy.security import (
    create_session_cookie,
    safe_next_path,
    session_is_valid,
    validate_production_access,
)
from growth_autopsy.store import WorkflowStore


def _production_settings(tmp_path) -> Settings:
    return Settings(
        environment="production",
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        app_username="operator",
        app_password="a-strong-production-password",
        session_secret="session-secret-with-more-than-32-characters",
        managed_configuration=True,
        enable_background_sync=False,
    ).resolve_paths(tmp_path)


def test_production_access_configuration_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="GA_APP_USERNAME"):
        validate_production_access(Settings(environment="production"))
    with pytest.raises(RuntimeError, match="at least 12"):
        validate_production_access(
            Settings(
                environment="production",
                app_username="operator",
                app_password="short",
                session_secret="session-secret-with-more-than-32-characters",
            )
        )


def test_signed_session_rejects_tampering_and_expiry(tmp_path) -> None:
    settings = _production_settings(tmp_path)
    cookie = create_session_cookie(settings, now=1_000)

    assert session_is_valid(cookie, settings, now=1_001)
    assert not session_is_valid(cookie + "tampered", settings, now=1_001)
    assert not session_is_valid("not-base64.signature", settings, now=1_001)
    assert not session_is_valid(cookie, settings, now=1_000 + 12 * 3600)
    assert safe_next_path("https://attacker.example") == "/"
    assert safe_next_path("//attacker.example") == "/"
    assert safe_next_path("/\\attacker.example") == "/"
    assert safe_next_path("/admin/#configuration") == "/admin/#configuration"


@pytest.mark.asyncio
async def test_production_routes_require_login_but_health_and_webhook_stay_public(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _production_settings(tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    settings_dependency = api_module.get_settings
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    app.dependency_overrides[settings_dependency] = lambda: settings
    app.dependency_overrides[get_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.8", 1234)),
            base_url="https://growth-autopsy.example",
            follow_redirects=False,
        ) as client:
            health = await client.get("/health")
            dashboard_api = await client.get("/internal/dashboard")
            dashboard_page = await client.get("/")
            webhook = await client.post("/webhooks/fathom", content=b"{}")
            failed_login = await client.post(
                "/login",
                content=urlencode(
                    {"username": "operator", "password": "wrong", "next": "/admin/"}
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            login = await client.post(
                "/login",
                content=urlencode(
                    {
                        "username": settings.app_username,
                        "password": settings.app_password,
                        "next": "/admin/",
                    }
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            admin = await client.get("/internal/admin/database")
            config_update = await client.put(
                "/internal/admin/config",
                json={"values": {"GA_AI_MODEL": "replacement"}},
            )
    finally:
        app.dependency_overrides.pop(settings_dependency, None)
        app.dependency_overrides.pop(get_store, None)

    assert health.status_code == 200
    assert dashboard_api.status_code == 401
    assert dashboard_page.status_code == 303
    assert dashboard_page.headers["location"].startswith("/login?")
    assert webhook.status_code != 401 or webhook.json().get("detail") != "Operator authentication required"
    assert failed_login.status_code == 401
    assert login.status_code == 303
    assert login.headers["location"] == "/admin/"
    assert "Secure" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert admin.status_code == 200
    assert config_update.status_code == 409
    assert admin.headers["strict-transport-security"].startswith("max-age=")
