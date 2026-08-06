from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AppointmentStatus(StrEnum):
    BOOKED = "BOOKED"
    NEEDS_INPUT = "NEEDS_INPUT"
    RESEARCH_SCHEDULED = "RESEARCH_SCHEDULED"
    RESEARCH_READY = "RESEARCH_READY"
    CANCELLED = "CANCELLED"
    CALL_COMPLETED = "CALL_COMPLETED"
    TRANSCRIPT_READY = "TRANSCRIPT_READY"
    ANALYSIS_RUNNING = "ANALYSIS_RUNNING"
    CONTENT_DRAFTED = "CONTENT_DRAFTED"
    FAILED = "FAILED"


class RecordingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    UNMATCHED = "UNMATCHED"
    ANALYSIS_RUNNING = "ANALYSIS_RUNNING"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    FAILED = "FAILED"


class ArtifactStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    APPROVED = "APPROVED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    FAILED = "FAILED"


class ArtifactDecision(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"


@dataclass(slots=True)
class Appointment:
    calendar_event_id: str
    calendar_id: str
    etag: str
    title: str
    company: str
    website: str
    founder_name: str
    founder_email: str
    founder_linkedin: str
    industry: str
    strategy_mode: str
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    source_payload: dict[str, Any]
    research_job_id: str | None = None
    research_start_at: datetime | None = None
    last_error: str | None = None


@dataclass(slots=True)
class Recording:
    recording_id: int
    webhook_id: str
    calendar_event_id: str | None
    meeting_title: str
    scheduled_start_at: datetime
    recording_start_at: datetime | None
    recording_end_at: datetime | None
    external_invitee_emails: list[str]
    transcript_path: str
    payload: dict[str, Any]
    status: RecordingStatus
    analysis_run_id: str | None = None
    last_error: str | None = None


@dataclass(slots=True)
class Artifact:
    id: int | None
    calendar_event_id: str
    kind: str
    title: str
    status: ArtifactStatus
    source_id: str = ""
    file_path: str = ""
    content: str = ""
    notes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
