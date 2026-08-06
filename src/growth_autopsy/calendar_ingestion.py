from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup

from .domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus
from .store import WorkflowStore


class CalendarGateway(Protocol):
    def list_events(self) -> list[dict[str, Any]]: ...


class ResearchScheduler(Protocol):
    async def create_research_job(
        self, appointment: Appointment, research_start_at: datetime, delivery_at: datetime
    ) -> str: ...

    async def update_research_job(
        self,
        job_id: str,
        appointment: Appointment,
        research_start_at: datetime,
        delivery_at: datetime,
    ) -> None: ...

    async def delete_job(self, job_id: str) -> None: ...


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Calendar date-time must include a timezone")
    return parsed


def _description_as_text(description: str) -> str:
    """Normalize both plain text and Google Calendar's HTML descriptions."""

    value = html.unescape(description or "")
    if re.search(r"</?[a-z][^>]*>", value, re.I):
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup.find_all(["br", "div", "p", "li"]):
            if tag.name == "br":
                tag.replace_with("\n")
            else:
                tag.append("\n")
        value = soup.get_text("")
    return value.replace("\xa0", " ")


def parse_description(description: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in _description_as_text(description).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().casefold()).strip("_")
        if normalized:
            fields[normalized] = value.strip()
    return fields


def _company_from_title(title: str, prefix: str) -> str:
    cleaned = title
    if prefix and cleaned.casefold().startswith(prefix.casefold()):
        cleaned = cleaned[len(prefix) :].strip()
    return re.split(r"\s+[–—-]\s+", cleaned, maxsplit=1)[0].strip()


def parse_calendar_event(
    event: dict[str, Any],
    *,
    calendar_id: str,
    title_prefix: str,
    diksha_email: str = "",
) -> Appointment | None:
    if event.get("status") == "cancelled":
        return None

    title = str(event.get("summary") or "").strip()
    fields = parse_description(str(event.get("description") or ""))
    marker = fields.get("automation", "").casefold() == "growth_autopsy"
    title_match = bool(title_prefix and title.casefold().startswith(title_prefix.casefold()))
    if not marker and not title_match:
        return None

    start_raw = (event.get("start") or {}).get("dateTime")
    end_raw = (event.get("end") or {}).get("dateTime")
    if not start_raw or not end_raw:
        return None

    attendees = event.get("attendees") or []
    owner_email = diksha_email.strip().casefold()
    external_attendees = [
        attendee
        for attendee in attendees
        if attendee.get("email")
        and attendee.get("email", "").strip().casefold() != owner_email
        and not attendee.get("self")
    ]
    first_attendee = external_attendees[0] if external_attendees else {}

    company = fields.get("company") or _company_from_title(title, title_prefix)
    website = fields.get("website", "")
    founder_email = fields.get("founder_email") or str(first_attendee.get("email") or "")
    founder_name = fields.get("founder") or fields.get("founder_name") or str(
        first_attendee.get("displayName") or ""
    )

    status = AppointmentStatus.BOOKED
    if not website or not company:
        status = AppointmentStatus.NEEDS_INPUT

    return Appointment(
        calendar_event_id=str(event["id"]),
        calendar_id=calendar_id,
        etag=str(event.get("etag") or event.get("updated") or ""),
        title=title,
        company=company,
        website=website,
        founder_name=founder_name,
        founder_email=founder_email,
        founder_linkedin=fields.get("founder_linkedin", fields.get("linkedin", "")),
        industry=fields.get("industry", ""),
        strategy_mode=fields.get("strategy_mode", "auto").casefold(),
        start_at=_parse_datetime(start_raw),
        end_at=_parse_datetime(end_raw),
        status=status,
        source_payload=event,
    )


class GoogleCalendarGateway:
    """Read-only Google Calendar adapter using an existing OAuth token file."""

    def __init__(
        self,
        token_file: Path,
        calendar_id: str,
        *,
        lookback_hours: int,
        lookahead_days: int,
    ):
        self.token_file = token_file
        self.calendar_id = calendar_id
        self.lookback_hours = lookback_hours
        self.lookahead_days = lookahead_days

    def list_events(self) -> list[dict[str, Any]]:
        if not self.token_file.exists():
            raise FileNotFoundError(
                f"Google OAuth token not found: {self.token_file}. "
                "Authorize the Hermes google-workspace skill first."
            )
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials.from_authorized_user_file(
            str(self.token_file),
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        now = datetime.now(UTC)
        params: dict[str, Any] = {
            "calendarId": self.calendar_id,
            "timeMin": (now - timedelta(hours=self.lookback_hours)).isoformat(),
            "timeMax": (now + timedelta(days=self.lookahead_days)).isoformat(),
            "singleEvents": True,
            "showDeleted": True,
            "maxResults": 2500,
        }
        items: list[dict[str, Any]] = []
        while True:
            result = service.events().list(**params).execute()
            items.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
            params["pageToken"] = page_token
        return items


@dataclass(slots=True)
class CalendarSyncResult:
    scanned: int = 0
    ignored: int = 0
    scheduled: int = 0
    updated: int = 0
    cancelled: int = 0
    needs_input: int = 0


class CalendarIngestionService:
    def __init__(
        self,
        gateway: CalendarGateway,
        store: WorkflowStore,
        hermes: ResearchScheduler,
        *,
        calendar_id: str,
        title_prefix: str,
        diksha_email: str,
        precall_start_minutes: int,
        precall_delivery_minutes: int,
    ):
        self.gateway = gateway
        self.store = store
        self.hermes = hermes
        self.calendar_id = calendar_id
        self.title_prefix = title_prefix
        self.diksha_email = diksha_email
        self.precall_start_minutes = precall_start_minutes
        self.precall_delivery_minutes = precall_delivery_minutes

    async def sync(self, now: datetime | None = None) -> CalendarSyncResult:
        current_time = now or datetime.now(UTC)
        events = await asyncio.to_thread(self.gateway.list_events)
        result = CalendarSyncResult(scanned=len(events))

        for event in events:
            event_id = str(event.get("id") or "")
            if not event_id:
                result.ignored += 1
                continue
            existing = self.store.get_appointment(event_id)
            if event.get("status") == "cancelled":
                if existing:
                    if existing.status == AppointmentStatus.CANCELLED:
                        continue
                    if existing.research_job_id:
                        await self.hermes.delete_job(existing.research_job_id)
                    self.store.cancel_appointment(event_id)
                    result.cancelled += 1
                else:
                    result.ignored += 1
                continue

            appointment = parse_calendar_event(
                event,
                calendar_id=self.calendar_id,
                title_prefix=self.title_prefix,
                diksha_email=self.diksha_email,
            )
            if appointment is None:
                result.ignored += 1
                continue

            changed = existing is None or existing.etag != appointment.etag
            if not changed:
                continue

            self.store.upsert_appointment(appointment)
            if appointment.status == AppointmentStatus.NEEDS_INPUT:
                if existing and existing.research_job_id:
                    await self.hermes.delete_job(existing.research_job_id)
                    self.store.clear_research_job(event_id)
                result.needs_input += 1
                continue

            research_start = max(
                current_time,
                appointment.start_at - timedelta(minutes=self.precall_start_minutes),
            )
            delivery_at = appointment.start_at - timedelta(
                minutes=self.precall_delivery_minutes
            )
            can_update = getattr(self.hermes, "can_update_job", lambda _: True)
            if existing and existing.research_job_id and can_update(existing.research_job_id):
                await self.hermes.update_research_job(
                    existing.research_job_id,
                    appointment,
                    research_start,
                    delivery_at,
                )
                job_id = existing.research_job_id
                result.updated += 1
            else:
                job_id = await self.hermes.create_research_job(
                    appointment,
                    research_start,
                    delivery_at,
                )
                result.scheduled += 1
            self.store.set_research_job(event_id, job_id, research_start)
            self.store.upsert_artifact(
                Artifact(
                    id=None,
                    calendar_event_id=event_id,
                    kind="precall_research",
                    title=f"{appointment.company} pre-call intelligence",
                    status=ArtifactStatus.SCHEDULED,
                    source_id=job_id,
                    notes=f"Scheduled to collect evidence at {research_start.isoformat()}",
                )
            )

        return result
