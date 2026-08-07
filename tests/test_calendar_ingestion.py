from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from growth_autopsy.calendar_ingestion import (
    CalendarIngestionService,
    calendar_conference_url,
    parse_calendar_event,
)
from growth_autopsy.domain import AppointmentStatus
from growth_autopsy.store import WorkflowStore


def calendar_event() -> dict:
    return {
        "id": "event-123",
        "etag": "etag-1",
        "status": "confirmed",
        "summary": "[GROWTH AUTOPSY] Acme – Alice Founder",
        "description": "\n".join(
            [
                "Automation: GROWTH_AUTOPSY",
                "Company Name: Acme",
                "Company Website: https://acme.example",
                "Founder Email: alice@acme.example",
                "Founder LinkedIn: https://linkedin.com/in/alice",
                "Meeting Agenda:",
                "- Review acquisition priorities",
                "- Identify conversion opportunities",
            ]
        ),
        "start": {"dateTime": "2026-08-20T15:00:00+05:30"},
        "end": {"dateTime": "2026-08-20T16:00:00+05:30"},
        "attendees": [
            {"email": "diksha@example.com", "displayName": "Diksha", "self": True},
            {"email": "alice@acme.example", "displayName": "Alice Founder"},
        ],
    }


def test_parse_structured_calendar_event() -> None:
    appointment = parse_calendar_event(
        calendar_event(),
        calendar_id="primary",
        title_prefix="[GROWTH AUTOPSY]",
        diksha_email="diksha@example.com",
    )

    assert appointment is not None
    assert appointment.company == "Acme"
    assert appointment.website == "https://acme.example"
    assert appointment.founder_name == "Alice Founder"
    assert appointment.founder_email == "alice@acme.example"
    assert appointment.founder_linkedin == "https://linkedin.com/in/alice"
    assert appointment.meeting_agenda == (
        "Review acquisition priorities\nIdentify conversion opportunities"
    )
    assert appointment.status == AppointmentStatus.BOOKED


def test_google_meet_link_is_extracted_from_calendar_event() -> None:
    event = calendar_event()
    event["conferenceData"] = {
        "entryPoints": [
            {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}
        ]
    }
    assert calendar_conference_url(event) == "https://meet.google.com/abc-defg-hij"


def test_missing_website_requires_input() -> None:
    event = calendar_event()
    event["description"] = "Automation: GROWTH_AUTOPSY\nCompany: Acme"
    appointment = parse_calendar_event(
        event,
        calendar_id="primary",
        title_prefix="[GROWTH AUTOPSY]",
    )

    assert appointment is not None
    assert appointment.status == AppointmentStatus.NEEDS_INPUT


def test_google_calendar_html_description_is_parsed() -> None:
    event = calendar_event()
    event["description"] = (
        "<div>Automation: GROWTH_AUTOPSY</div>"
        "<div>Company: Acme</div>"
        '<div>Website: <a href="https://acme.example">https://acme.example</a></div>'
        "<div>Founder: Alice Founder</div>"
    )
    appointment = parse_calendar_event(
        event,
        calendar_id="primary",
        title_prefix="[GROWTH AUTOPSY]",
    )

    assert appointment is not None
    assert appointment.website == "https://acme.example"
    assert appointment.founder_name == "Alice Founder"


class FakeCalendar:
    def __init__(self, events: list[dict]):
        self.events = events

    def list_events(self) -> list[dict]:
        return deepcopy(self.events)


class FakeHermes:
    def __init__(self):
        self.created: list[tuple] = []
        self.updated: list[tuple] = []
        self.deleted: list[str] = []

    async def create_research_job(self, appointment, research_start_at, delivery_at):
        self.created.append((appointment, research_start_at, delivery_at))
        return "job-1"

    async def update_research_job(self, job_id, appointment, research_start_at, delivery_at):
        self.updated.append((job_id, appointment, research_start_at, delivery_at))

    async def delete_job(self, job_id):
        self.deleted.append(job_id)

    @staticmethod
    def can_update_job(job_id):
        return True


@pytest.mark.asyncio
async def test_sync_creates_updates_and_cancels_research_job(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    gateway = FakeCalendar([calendar_event()])
    hermes = FakeHermes()
    service = CalendarIngestionService(
        gateway,
        store,
        hermes,  # type: ignore[arg-type]
        calendar_id="primary",
        title_prefix="[GROWTH AUTOPSY]",
        diksha_email="diksha@example.com",
        precall_start_minutes=60,
        precall_delivery_minutes=30,
    )
    now = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)

    first = await service.sync(now=now)
    assert first.scheduled == 1
    assert len(hermes.created) == 1
    stored = store.get_appointment("event-123")
    assert stored is not None
    assert stored.research_job_id == "job-1"
    assert stored.status == AppointmentStatus.RESEARCH_SCHEDULED
    assert store.get_artifact_by_kind("event-123", "precall_research") is not None

    gateway.events[0]["etag"] = "etag-2"
    gateway.events[0]["start"]["dateTime"] = "2026-08-20T16:00:00+05:30"
    second = await service.sync(now=now)
    assert second.updated == 1
    assert hermes.updated[0][0] == "job-1"

    gateway.events[0] = {"id": "event-123", "status": "cancelled"}
    third = await service.sync(now=now)
    assert third.cancelled == 1
    assert hermes.deleted == ["job-1"]
    assert store.get_appointment("event-123").status == AppointmentStatus.CANCELLED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_deleted_dashboard_meeting_is_not_reimported_by_calendar_sync(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    gateway = FakeCalendar([calendar_event()])
    hermes = FakeHermes()
    service = CalendarIngestionService(
        gateway,
        store,
        hermes,  # type: ignore[arg-type]
        calendar_id="primary",
        title_prefix="[GROWTH AUTOPSY]",
        diksha_email="diksha@example.com",
        precall_start_minutes=60,
        precall_delivery_minutes=30,
    )

    first = await service.sync(now=datetime(2026, 8, 20, 7, 0, tzinfo=UTC))
    assert first.scheduled == 1
    assert store.delete_appointment("event-123") is True

    gateway.events[0]["etag"] = "etag-after-delete"
    second = await service.sync(now=datetime(2026, 8, 20, 7, 5, tzinfo=UTC))

    assert second.ignored == 1
    assert second.scheduled == 0
    assert store.get_appointment("event-123") is None
    assert len(hermes.created) == 1
