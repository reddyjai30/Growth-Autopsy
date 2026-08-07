from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from growth_autopsy import calendar_auth, cli


class FakeCredentials:
    def to_json(self) -> str:
        return json.dumps(
            {
                "token": "do-not-print-this-token",
                "refresh_token": "do-not-print-this-refresh-token",
            }
        )


class FakeFlow:
    def __init__(self) -> None:
        self.run_kwargs: dict = {}

    def run_local_server(self, **kwargs):
        self.run_kwargs = kwargs
        return FakeCredentials()


def test_calendar_auth_uses_browser_flow_and_writes_private_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_secret = tmp_path / "client-secret.json"
    client_secret.write_text('{"installed": {}}', encoding="utf-8")
    token_file = tmp_path / "secrets" / "google-token.json"
    flow = FakeFlow()
    captured_secret_path: list[Path] = []

    def fake_create(candidate: Path) -> FakeFlow:
        captured_secret_path.append(candidate)
        return flow

    monkeypatch.setattr(calendar_auth, "_create_installed_app_flow", fake_create)

    result = calendar_auth.authorize_google_calendar(client_secret, token_file)

    assert result == token_file.resolve()
    assert captured_secret_path == [client_secret.resolve()]
    assert flow.run_kwargs["host"] == "localhost"
    assert flow.run_kwargs["port"] == 0
    assert flow.run_kwargs["open_browser"] is True
    assert flow.run_kwargs["access_type"] == "offline"
    assert flow.run_kwargs["prompt"] == "consent"
    assert json.loads(token_file.read_text(encoding="utf-8"))["refresh_token"]
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_calendar_auth_rejects_missing_client_secret(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="client secret JSON not found"):
        calendar_auth.authorize_google_calendar(
            tmp_path / "missing.json",
            tmp_path / "token.json",
        )


def test_calendar_auth_refuses_to_overwrite_token_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_secret = tmp_path / "client-secret.json"
    client_secret.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "google-token.json"
    token_file.write_text("existing-token", encoding="utf-8")
    monkeypatch.setattr(
        calendar_auth,
        "_create_installed_app_flow",
        lambda _: pytest.fail("OAuth flow must not start when overwrite is refused"),
    )

    with pytest.raises(FileExistsError, match="Re-run with --force"):
        calendar_auth.authorize_google_calendar(client_secret, token_file)

    assert token_file.read_text(encoding="utf-8") == "existing-token"


def test_calendar_auth_force_replaces_existing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_secret = tmp_path / "client-secret.json"
    client_secret.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "google-token.json"
    token_file.write_text("existing-token", encoding="utf-8")
    monkeypatch.setattr(
        calendar_auth,
        "_create_installed_app_flow",
        lambda _: FakeFlow(),
    )

    calendar_auth.authorize_google_calendar(client_secret, token_file, force=True)

    assert "do-not-print-this-token" in token_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_calendar_auth_cli_prints_metadata_but_not_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client_secret = tmp_path / "desktop-client.json"
    client_secret.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "google-token.json"

    class FakeSettings:
        google_token_file = token_file

    monkeypatch.setattr(cli, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        cli,
        "authorize_google_calendar",
        lambda candidate, destination, *, force: destination,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["growth-autopsy", "calendar-auth", "--client-secret", str(client_secret)],
    )

    cli.main()

    payload = capsys.readouterr().out
    assert json.loads(payload) == {
        "status": "authorized",
        "token_file": str(token_file),
        "scope": calendar_auth.CALENDAR_READONLY_SCOPE,
    }
    assert "do-not-print-this" not in payload


@pytest.mark.asyncio
async def test_calendar_check_previews_matches_without_workflow_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSettings:
        google_token_file = tmp_path / "google-token.json"
        google_calendar_id = "primary"
        calendar_lookback_hours = 24
        calendar_lookahead_days = 30
        calendar_title_prefix = "[GROWTH AUTOPSY]"
        diksha_email = "diksha@example.com"

    event = {
        "id": "event-preview",
        "etag": "1",
        "status": "confirmed",
        "summary": "[GROWTH AUTOPSY] Acme",
        "description": "Company: Acme\nWebsite: https://acme.example",
        "start": {"dateTime": "2026-08-20T15:00:00+05:30"},
        "end": {"dateTime": "2026-08-20T16:00:00+05:30"},
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
    }

    class FakeGateway:
        def __init__(self, *args, **kwargs):
            pass

        def list_events(self):
            return [event, {"id": "ignored", "summary": "Lunch"}]

    monkeypatch.setattr(cli, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(cli, "GoogleCalendarGateway", FakeGateway)

    result = await cli._calendar_check()

    assert result["events_scanned"] == 2
    assert result["workflow_state_changed"] is False
    assert result["matching_events"][0]["event_id"] == "event-preview"
    assert result["matching_events"][0]["conference_url"].startswith("https://meet.google.com/")
