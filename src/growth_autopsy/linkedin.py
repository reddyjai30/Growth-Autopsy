from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


LINKEDIN_AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_URL = "https://api.linkedin.com"
LINKEDIN_SCOPES = ("openid", "profile", "w_member_social")
MAX_LINKEDIN_COMMENTARY_CHARACTERS = 3_000


class LinkedInError(RuntimeError):
    pass


class LinkedInAuthorizationError(LinkedInError):
    pass


class LinkedInPublishError(LinkedInError):
    pass


class LinkedInAmbiguousPublishError(LinkedInPublishError):
    """The request may have reached LinkedIn, so an automatic retry is unsafe."""


@dataclass(frozen=True, slots=True)
class LinkedInToken:
    access_token: str
    person_urn: str
    expires_at: datetime
    scope: str

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(seconds=60)


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise LinkedInAuthorizationError("LinkedIn token has an invalid expiry") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class LinkedInTokenStore:
    """Persist the member token locally without exposing it through the admin API."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> LinkedInToken:
        if not self.path.is_file():
            raise LinkedInAuthorizationError(
                "LinkedIn is not authorized. Connect the profile from Admin → Configuration."
            )
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LinkedInAuthorizationError("LinkedIn token file is unreadable") from exc
        if not isinstance(payload, dict):
            raise LinkedInAuthorizationError("LinkedIn token file is incomplete")
        access_token = str(payload.get("access_token") or "").strip()
        person_urn = str(payload.get("person_urn") or "").strip()
        scope = str(payload.get("scope") or "").strip()
        if not access_token or not re.fullmatch(r"urn:li:person:[A-Za-z0-9_-]+", person_urn):
            raise LinkedInAuthorizationError("LinkedIn token file is incomplete")
        token = LinkedInToken(
            access_token=access_token,
            person_urn=person_urn,
            expires_at=_parse_datetime(payload.get("expires_at")),
            scope=scope,
        )
        if token.expired:
            raise LinkedInAuthorizationError(
                "LinkedIn authorization has expired. Reconnect the profile from "
                "Admin → Configuration."
            )
        granted = set(scope.split())
        if scope and "w_member_social" not in granted:
            raise LinkedInAuthorizationError(
                "LinkedIn authorization is missing the w_member_social permission"
            )
        return token

    def inspect(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"authorized": False, "expired": False, "person_urn": "", "expires_at": ""}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise LinkedInAuthorizationError("LinkedIn token file is incomplete")
            expires_at = _parse_datetime(payload.get("expires_at"))
            person_urn = str(payload.get("person_urn") or "")
            authorized = bool(
                re.fullmatch(r"urn:li:person:[A-Za-z0-9_-]+", person_urn)
                and payload.get("access_token")
            )
            return {
                "authorized": authorized,
                "expired": datetime.now(UTC) >= expires_at - timedelta(seconds=60),
                "person_urn": person_urn if authorized else "",
                "expires_at": expires_at.isoformat() if authorized else "",
            }
        except (OSError, json.JSONDecodeError, LinkedInAuthorizationError):
            return {"authorized": False, "expired": False, "person_urn": "", "expires_at": ""}

    def save(self, token_payload: dict[str, Any], member_sub: str) -> LinkedInToken:
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise LinkedInAuthorizationError("LinkedIn did not return an access token")
        member_id = str(member_sub or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", member_id):
            raise LinkedInAuthorizationError("LinkedIn did not return a valid member identifier")
        try:
            expires_in = int(token_payload.get("expires_in") or 0)
        except (TypeError, ValueError) as exc:
            raise LinkedInAuthorizationError("LinkedIn returned an invalid token lifetime") from exc
        if expires_in <= 0:
            raise LinkedInAuthorizationError("LinkedIn did not return a token lifetime")
        scope = str(token_payload.get("scope") or " ".join(LINKEDIN_SCOPES)).strip()
        token = LinkedInToken(
            access_token=access_token,
            person_urn=f"urn:li:person:{member_id}",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            scope=scope,
        )
        document = {
            "access_token": token.access_token,
            "person_urn": token.person_urn,
            "expires_at": token.expires_at.isoformat(),
            "scope": token.scope,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)
        return token


class LinkedInClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        api_version: str = "202607",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.api_version = api_version.strip()
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def authorization_url(self, state: str) -> str:
        if not self.configured:
            raise LinkedInAuthorizationError(
                "LinkedIn client ID, client secret and redirect URI are required"
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "scope": " ".join(LINKEDIN_SCOPES),
            }
        )
        return f"{LINKEDIN_AUTHORIZATION_URL}?{query}"

    async def exchange_code(self, code: str) -> tuple[dict[str, Any], str]:
        if not self.configured:
            raise LinkedInAuthorizationError("LinkedIn OAuth is not configured")
        async with httpx.AsyncClient(
            timeout=30,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                LINKEDIN_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if response.is_error:
                raise LinkedInAuthorizationError(
                    f"LinkedIn token exchange failed ({response.status_code}): "
                    f"{response.text[:400]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise LinkedInAuthorizationError(
                    "LinkedIn token exchange returned an invalid response"
                ) from exc
            if not isinstance(payload, dict):
                raise LinkedInAuthorizationError(
                    "LinkedIn token exchange returned an invalid response"
                )
            access_token = str(payload.get("access_token") or "")
            if not access_token:
                raise LinkedInAuthorizationError("LinkedIn did not return an access token")
            profile = await client.get(
                f"{LINKEDIN_API_URL}/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        if profile.is_error:
            raise LinkedInAuthorizationError(
                f"LinkedIn profile lookup failed ({profile.status_code}): {profile.text[:400]}"
            )
        try:
            profile_payload = profile.json()
        except ValueError as exc:
            raise LinkedInAuthorizationError(
                "LinkedIn profile lookup returned an invalid response"
            ) from exc
        if not isinstance(profile_payload, dict):
            raise LinkedInAuthorizationError(
                "LinkedIn profile lookup returned an invalid response"
            )
        member_sub = str(profile_payload.get("sub") or "").strip()
        if not member_sub:
            raise LinkedInAuthorizationError("LinkedIn profile did not return a member identifier")
        return payload, member_sub

    async def publish_text(self, token: LinkedInToken, commentary: str) -> dict[str, str]:
        if token.expired:
            raise LinkedInAuthorizationError("LinkedIn authorization has expired")
        clean = commentary.strip()
        if not clean:
            raise LinkedInPublishError("LinkedIn post copy is empty")
        if len(clean) > MAX_LINKEDIN_COMMENTARY_CHARACTERS:
            raise LinkedInPublishError(
                f"LinkedIn post is {len(clean)} characters; the safe limit is "
                f"{MAX_LINKEDIN_COMMENTARY_CHARACTERS}"
            )
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self.api_version,
        }
        body = {
            "author": token.person_urn,
            "commentary": clean,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            async with httpx.AsyncClient(
                base_url=LINKEDIN_API_URL,
                timeout=30,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.post("/rest/posts", headers=headers, json=body)
        except httpx.TransportError as exc:
            raise LinkedInAmbiguousPublishError(
                "LinkedIn did not confirm whether the post was created. Check the profile "
                "before retrying to avoid a duplicate."
            ) from exc
        if response.status_code != 201:
            raise LinkedInPublishError(
                f"LinkedIn post creation failed ({response.status_code}): {response.text[:500]}"
            )
        post_id = str(response.headers.get("x-restli-id") or "").strip()
        if not post_id:
            raise LinkedInAmbiguousPublishError(
                "LinkedIn accepted the post but did not return its ID. Check the profile "
                "before retrying."
            )
        return {
            "post_id": post_id,
            "url": f"https://www.linkedin.com/feed/update/{post_id}/",
        }


def extract_linkedin_commentary(markdown: str) -> str:
    match = re.search(
        r"(?ms)^##\s+Draft Post\s*$\n(.*?)(?=^##\s+Public Claim Ledger\s*$)",
        markdown,
    )
    if not match:
        raise LinkedInPublishError("Approved LinkedIn artifact is missing its Draft Post section")
    commentary = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S).strip()
    return commentary.strip()
