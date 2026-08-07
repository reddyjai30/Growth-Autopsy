from datetime import UTC, datetime
import json

import httpx
import pytest

from growth_autopsy.domain import Appointment, AppointmentStatus
from growth_autopsy.hermes import HermesClient


def appointment() -> Appointment:
    return Appointment(
        calendar_event_id="event-123",
        calendar_id="primary",
        etag="etag",
        title="[GROWTH AUTOPSY] Acme – Alice",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="alice@acme.example",
        founder_linkedin="",
        industry="Ecommerce",
        strategy_mode="auto",
        start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        status=AppointmentStatus.BOOKED,
        source_payload={},
    )


@pytest.mark.asyncio
async def test_create_research_job_uses_hermes_jobs_api() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"job": {"id": "job-abc"}})

    client = HermesClient(
        "http://hermes.local",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    research_at = datetime(2026, 8, 20, 8, 30, tzinfo=UTC)
    delivery_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

    job_id = await client.create_research_job(appointment(), research_at, delivery_at)

    assert job_id == "job-abc"
    assert captured[0].url.path == "/api/jobs"
    assert captured[0].headers["authorization"] == "Bearer secret"
    assert b"founder-precall-research" in captured[0].content


@pytest.mark.asyncio
async def test_start_precall_run_sends_evidence_only() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"run_id": "run-precall"})

    client = HermesClient(
        "http://hermes.local",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    evidence = {"website": {"final_url": "https://acme.example"}, "traffic": {"status": "unavailable"}}

    run_id = await client.start_precall_run(appointment(), evidence)

    assert run_id == "run-precall"
    payload = json.loads(captured[0].content)
    assert captured[0].url.path == "/v1/runs"
    assert "Do not browse" in payload["instructions"]
    assert "https://acme.example" in payload["input"]
    assert "model" not in payload
    assert "provider" not in payload
