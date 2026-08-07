from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from growth_autopsy.ai import AIClient, AIClientError, validate_precall_report
from growth_autopsy.domain import Appointment, AppointmentStatus


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
        meeting_agenda="Review acquisition and conversion priorities",
    )


def valid_report() -> str:
    return "\n".join([
        "## Founder & Company Background", "Founder and company context.",
        "## Executive Marketing Brief", "- Clear positioning",
        "## Website & Conversion Review", "### Positioning & Messaging", "Observed.",
        "## SEO & Search Visibility", "### Technical SEO", "Observed.",
        "## Traffic & Channel Intelligence", "No licensed estimate.",
        "## Paid Media & Creative Signals", "No verified active ads.",
        "## Social, Email & Technology", "Observed technology.",
        "## Competitor Landscape", "Candidates require validation.",
        "## 10 Positives", *[f"{index}. Positive {index}" for index in range(1, 11)],
        "## 10 Growth Gaps", *[f"{index}. Gap {index}" for index in range(1, 11)],
        "## 5 Discovery Questions", *[f"{index}. Question {index}?" for index in range(1, 6)],
        "## Recommended Call Agenda", "1. Discuss priorities",
        "## Data Boundaries & Access Needed", "Analytics access is required.",
        "## Sources", "- [Company website](https://acme.example)",
    ])


@pytest.mark.asyncio
async def test_direct_ai_synthesis_sends_only_supplied_evidence() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_report()}}]},
        )

    client = AIClient(
        "https://api.example.test/v1",
        "secret",
        "example-model",
        transport=httpx.MockTransport(handler),
    )
    report = await client.synthesize_precall(
        appointment(),
        {"website": {"final_url": "https://acme.example"}},
    )

    assert report.startswith("## Founder & Company Background")
    assert captured[0].url == "https://api.example.test/v1/chat/completions"
    payload = json.loads(captured[0].content)
    assert payload["model"] == "example-model"
    assert "https://acme.example" in payload["messages"][1]["content"]
    assert "Review acquisition and conversion priorities" in payload["messages"][1]["content"]
    assert "untrusted evidence" in payload["messages"][0]["content"]
    assert "## SEO & Search Visibility" in payload["messages"][0]["content"]


@pytest.mark.asyncio
async def test_direct_ai_synthesis_repairs_one_invalid_document() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        report = "## 10 Positives\n1. Incomplete" if request_count == 1 else valid_report()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": report}}]},
        )

    client = AIClient(
        "https://api.example.test/v1",
        "secret",
        "example-model",
        transport=httpx.MockTransport(handler),
    )
    report = await client.synthesize_precall(appointment(), {})

    assert request_count == 2
    assert report == valid_report()


@pytest.mark.asyncio
async def test_direct_ai_synthesis_requires_configuration() -> None:
    with pytest.raises(AIClientError, match="GA_AI_API_KEY"):
        await AIClient("https://api.example.test/v1", "", "").synthesize_precall(
            appointment(), {}
        )


def test_precall_report_contract_rejects_wrong_counts() -> None:
    with pytest.raises(AIClientError, match="contains 1 list items"):
        validate_precall_report(
            "## 10 Positives\n1. One\n"
            "## 10 Growth Gaps\n1. One\n"
            "## 5 Discovery Questions\n1. One?\n"
        )
