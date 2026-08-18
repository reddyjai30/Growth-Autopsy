from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .config import Settings


SESSION_COOKIE = "ga_operator_session"
PUBLIC_PATHS = frozenset({"/health", "/webhooks/fathom", "/login"})


def production_access_enabled(settings: Settings) -> bool:
    return settings.environment.strip().casefold() == "production"


def validate_production_access(settings: Settings) -> None:
    """Fail closed when a production deployment lacks operator credentials."""

    if not production_access_enabled(settings):
        return
    missing = [
        name
        for name, value in (
            ("GA_APP_USERNAME", settings.app_username),
            ("GA_APP_PASSWORD", settings.app_password),
            ("GA_SESSION_SECRET", settings.session_secret),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError(
            "Production access control is incomplete: " + ", ".join(missing)
        )
    if len(settings.app_password) < 12:
        raise RuntimeError("GA_APP_PASSWORD must contain at least 12 characters")
    if len(settings.session_secret) < 32:
        raise RuntimeError("GA_SESSION_SECRET must contain at least 32 characters")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _encode(digest)


def create_session_cookie(settings: Settings, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = _encode(
        json.dumps(
            {
                "sub": settings.app_username,
                "iat": issued_at,
                "exp": issued_at + settings.session_ttl_hours * 3600,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{payload}.{_signature(payload, settings.session_secret)}"


def session_is_valid(
    value: str,
    settings: Settings,
    *,
    now: int | None = None,
) -> bool:
    if not value or "." not in value or not production_access_enabled(settings):
        return False
    payload, supplied_signature = value.rsplit(".", 1)
    expected_signature = _signature(payload, settings.session_secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return False
    try:
        claims: dict[str, Any] = json.loads(_decode(payload))
        current_time = int(time.time() if now is None else now)
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error):
        return False
    return bool(
        hmac.compare_digest(str(claims.get("sub", "")), settings.app_username)
        and issued_at <= current_time
        and expires_at > current_time
        and expires_at - issued_at == settings.session_ttl_hours * 3600
    )


def credentials_are_valid(username: str, password: str, settings: Settings) -> bool:
    username_valid = hmac.compare_digest(username, settings.app_username)
    password_valid = hmac.compare_digest(password, settings.app_password)
    return bool(production_access_enabled(settings) and username_valid and password_valid)


def safe_next_path(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.path in {"/login", "/logout"}
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return "/"
    return candidate


@dataclass(frozen=True, slots=True)
class LoginFields:
    username: str
    password: str
    next_path: str
