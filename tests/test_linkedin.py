from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from growth_autopsy.api import app, get_store
from growth_autopsy.config import Settings, get_settings
from growth_autopsy.controller import (
    publish_approved_distribution,
    resolve_linkedin_publication_uncertainty,
)
from growth_autopsy.domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus
from growth_autopsy.linkedin import (
    LINKEDIN_AUTHORIZATION_URL,
    LinkedInClient,
    LinkedInTokenStore,
    extract_linkedin_commentary,
)
from growth_autopsy.notion import NotionClient
from growth_autopsy.store import WorkflowStore


def _appointment() -> Appointment:
    return Appointment(
        calendar_event_id="linkedin-event",
        calendar_id="primary",
        etag="1",
        title="[GROWTH AUTOPSY] Acme",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="alice@acme.example",
        founder_linkedin="https://linkedin.com/in/alice",
        industry="SaaS",
        strategy_mode="case_study_only",
        start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        status=AppointmentStatus.CONTENT_DRAFTED,
        source_payload={},
    )


def _linkedin_artifact() -> str:
    return """## Draft Post
<!-- linkedin_mode: founder_story -->
Alice built Acme from a customer problem she understood first-hand.

That founder knowledge is now becoming a repeatable growth system.

Full Growth Autopsy in the comments.

#GrowthAutopsy #B2BGrowth #FounderLed
## Public Claim Ledger
- Approved report Section 2 — Founder Fact — public-safe.
## Approval Checklist
- Founder approval complete.
- This post has not been published.
"""


def test_authorization_url_uses_exact_redirect_state_and_member_scope() -> None:
    client = LinkedInClient(
        "client-id",
        "client-secret",
        "http://localhost:8787/internal/linkedin/oauth/callback",
    )

    url = urlsplit(client.authorization_url("csrf-value"))
    query = parse_qs(url.query)

    assert f"{url.scheme}://{url.netloc}{url.path}" == LINKEDIN_AUTHORIZATION_URL
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [
        "http://localhost:8787/internal/linkedin/oauth/callback"
    ]
    assert query["state"] == ["csrf-value"]
    assert set(query["scope"][0].split()) == {"openid", "profile", "w_member_social"}


@pytest.mark.asyncio
async def test_oauth_start_is_local_only_and_stores_only_a_state_hash(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        linkedin_client_id="client-id",
        linkedin_client_secret="client-secret",
        linkedin_token_file=tmp_path / "linkedin-token.json",
        linkedin_enabled=True,
    ).resolve_paths(tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8787",
            follow_redirects=False,
        ) as local:
            started = await local.get("/internal/linkedin/oauth/start")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 1234)),
            base_url="http://example.test",
            follow_redirects=False,
        ) as remote:
            rejected = await remote.get("/internal/linkedin/oauth/start")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_store, None)

    state_value = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    stored = store.get_setting("linkedin_oauth_state_sha256")
    assert started.status_code == 302
    assert started.headers["location"].startswith(LINKEDIN_AUTHORIZATION_URL)
    assert stored and stored != state_value
    assert len(stored) == 64
    assert rejected.status_code == 403


@pytest.mark.asyncio
async def test_oauth_exchange_token_storage_and_post_request(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/v2/accessToken":
            assert b"code=one-time-code" in request.content
            assert b"client_secret=client-secret" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "member-access-token",
                    "expires_in": 5_184_000,
                    "scope": "openid profile w_member_social",
                },
            )
        if request.url.path == "/v2/userinfo":
            assert request.headers["authorization"] == "Bearer member-access-token"
            return httpx.Response(200, json={"sub": "member_123"})
        assert request.url.path == "/rest/posts"
        assert request.headers["linkedin-version"] == "202607"
        body = json.loads(request.content)
        assert body["author"] == "urn:li:person:member_123"
        assert body["visibility"] == "PUBLIC"
        assert body["lifecycleState"] == "PUBLISHED"
        return httpx.Response(
            201,
            headers={"x-restli-id": "urn:li:share:123456789"},
        )

    client = LinkedInClient(
        "client-id",
        "client-secret",
        "http://localhost:8787/internal/linkedin/oauth/callback",
        transport=httpx.MockTransport(handler),
    )
    payload, member_sub = await client.exchange_code("one-time-code")
    tokens = LinkedInTokenStore(tmp_path / "secrets" / "linkedin-token.json")
    saved = tokens.save(payload, member_sub)
    published = await client.publish_text(saved, "An approved founder post.")

    assert published["post_id"] == "urn:li:share:123456789"
    assert published["url"].endswith("urn:li:share:123456789/")
    assert tokens.load().person_urn == "urn:li:person:member_123"
    assert stat.S_IMODE(tokens.path.stat().st_mode) == 0o600
    assert len(requests) == 3


def test_extract_commentary_publishes_only_the_approved_draft_section() -> None:
    commentary = extract_linkedin_commentary(_linkedin_artifact())

    assert "linkedin_mode" not in commentary
    assert "Public Claim Ledger" not in commentary
    assert "Approval Checklist" not in commentary
    assert "Full Growth Autopsy in the comments." in commentary


@pytest.mark.asyncio
async def test_distribution_publishes_notion_then_linkedin_exactly_once(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        notion_api_key="notion-secret",
        notion_parent_page_id="parent-page",
        linkedin_client_id="client-id",
        linkedin_client_secret="client-secret",
        linkedin_redirect_uri="http://localhost:8787/internal/linkedin/oauth/callback",
        linkedin_token_file=tmp_path / "secrets" / "linkedin-token.json",
        linkedin_enabled=True,
        linkedin_publish_after_notion=True,
    ).resolve_paths(tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    appointment = _appointment()
    store.upsert_appointment(appointment)
    store.set_setting(f"strategy_intent:{appointment.calendar_event_id}", "case_study_only")
    for kind, content in (
        ("growth_autopsy", "# Approved Growth Intelligence Report"),
        ("linkedin_post", _linkedin_artifact()),
    ):
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind=kind,
                title=kind,
                status=ArtifactStatus.APPROVED,
                content=content,
            )
        )
    tokens = LinkedInTokenStore(settings.linkedin_token_file)
    tokens.save(
        {
            "access_token": "member-token",
            "expires_in": 5_184_000,
            "scope": "openid profile w_member_social",
        },
        "member_123",
    )
    calls = {"notion": 0, "linkedin": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.notion.com":
            calls["notion"] += 1
            return httpx.Response(
                200,
                json={"id": "notion-page-1", "url": "https://notion.so/acme-report"},
            )
        calls["linkedin"] += 1
        body = json.loads(request.content)
        assert "Full Growth Autopsy in the comments." in body["commentary"]
        assert "https://notion.so/acme-report" not in body["commentary"]
        return httpx.Response(
            201,
            headers={"x-restli-id": "urn:li:share:123456789"},
        )

    transport = httpx.MockTransport(handler)
    notion = NotionClient(
        "notion-secret",
        "parent-page",
        transport=transport,
    )
    linkedin = LinkedInClient(
        "client-id",
        "client-secret",
        settings.linkedin_redirect_uri,
        transport=transport,
    )

    first = await publish_approved_distribution(
        settings,
        store,
        appointment.calendar_event_id,
        notion=notion,
        linkedin=linkedin,
        token_store=tokens,
    )
    second = await publish_approved_distribution(
        settings,
        store,
        appointment.calendar_event_id,
        notion=notion,
        linkedin=linkedin,
        token_store=tokens,
    )

    assert first["status"] == "published"
    assert second["status"] == "published"
    assert second["notion"]["status"] == "already_published"
    assert second["linkedin"]["status"] == "already_published"
    assert calls == {"notion": 1, "linkedin": 1}
    stored_appointment = store.get_appointment(appointment.calendar_event_id)
    assert stored_appointment is not None
    assert stored_appointment.status == AppointmentStatus.PUBLISHED
    publication = store.get_artifact_by_kind(
        appointment.calendar_event_id,
        "linkedin_publication",
    )
    assert publication is not None
    assert publication.source_id == "urn:li:share:123456789"
    assert publication.content.endswith("urn:li:share:123456789/")


@pytest.mark.asyncio
async def test_uncertain_publish_can_record_a_verified_existing_post(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        linkedin_enabled=True,
    )
    store = WorkflowStore(settings.database_path)
    store.initialize()
    appointment = _appointment()
    store.upsert_appointment(appointment)
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=appointment.calendar_event_id,
            kind="linkedin_publication",
            title="LinkedIn publication",
            status=ArtifactStatus.REVISION_REQUESTED,
            source_id="publish:uncertain",
            notes="Check the connected profile before retrying",
        )
    )
    post_url = (
        "https://www.linkedin.com/posts/example-growth-autopsy-"
        "activity-7491108769461055488-m2M4/"
    )

    result = await resolve_linkedin_publication_uncertainty(
        settings,
        store,
        appointment.calendar_event_id,
        "published",
        post_url=post_url,
    )

    assert result["status"] == "recorded"
    artifact = store.get_artifact_by_kind(
        appointment.calendar_event_id,
        "linkedin_publication",
    )
    assert artifact is not None
    assert artifact.status == ArtifactStatus.READY
    assert artifact.source_id == "urn:li:activity:7491108769461055488"
    assert artifact.content == post_url
    stored_appointment = store.get_appointment(appointment.calendar_event_id)
    assert stored_appointment is not None
    assert stored_appointment.status == AppointmentStatus.PUBLISHED
