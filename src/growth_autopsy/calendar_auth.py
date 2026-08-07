from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


def _create_installed_app_flow(client_secret_file: Path):
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=[CALENDAR_READONLY_SCOPE],
    )


def _write_authorized_user_token(token_file: Path, credentials: Any) -> None:
    """Persist an OAuth token atomically without exposing it in process output."""

    token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=token_file.parent,
            prefix=f".{token_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(credentials.to_json())
            temporary.flush()
            os.fsync(temporary.fileno())

        os.replace(temporary_path, token_file)
        os.chmod(token_file, 0o600)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def authorize_google_calendar(
    client_secret_file: Path,
    token_file: Path,
    *,
    force: bool = False,
) -> Path:
    """Run Google's browser OAuth flow and save a Calendar read-only token."""

    client_secret_file = client_secret_file.expanduser().resolve()
    token_file = token_file.expanduser().resolve()
    if not client_secret_file.is_file():
        raise FileNotFoundError(
            f"Google OAuth client secret JSON not found: {client_secret_file}"
        )
    if token_file.exists() and not force:
        raise FileExistsError(
            f"Google OAuth token already exists: {token_file}. "
            "Re-run with --force only if you intend to replace it."
        )

    flow = _create_installed_app_flow(client_secret_file)
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message=(
            "Open this URL in your browser if it did not open automatically:\n{url}"
        ),
        success_message=(
            "Growth Autopsy has read-only Calendar access. You may close this window."
        ),
    )
    _write_authorized_user_token(token_file, credentials)
    return token_file
