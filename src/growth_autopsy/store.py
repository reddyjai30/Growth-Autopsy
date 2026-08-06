from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .domain import (
    Artifact,
    ArtifactStatus,
    Appointment,
    AppointmentStatus,
    Recording,
    RecordingStatus,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class WorkflowStore:
    """SQLite-backed workflow state shared by the controller and CLI."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS appointments (
                    calendar_event_id TEXT PRIMARY KEY,
                    calendar_id TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    website TEXT NOT NULL,
                    founder_name TEXT NOT NULL,
                    founder_email TEXT NOT NULL,
                    founder_linkedin TEXT NOT NULL,
                    industry TEXT NOT NULL,
                    strategy_mode TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    research_job_id TEXT,
                    research_start_at TEXT,
                    source_payload_json TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_appointments_start
                    ON appointments(start_at);
                CREATE INDEX IF NOT EXISTS idx_appointments_status
                    ON appointments(status);

                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    first_received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recordings (
                    recording_id INTEGER PRIMARY KEY,
                    webhook_id TEXT NOT NULL,
                    calendar_event_id TEXT,
                    meeting_title TEXT NOT NULL,
                    scheduled_start_at TEXT NOT NULL,
                    recording_start_at TEXT,
                    recording_end_at TEXT,
                    external_invitee_emails_json TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    analysis_run_id TEXT,
                    last_error TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(calendar_event_id)
                        REFERENCES appointments(calendar_event_id)
                );

                CREATE INDEX IF NOT EXISTS idx_recordings_appointment
                    ON recordings(calendar_event_id);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calendar_event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(calendar_event_id)
                        REFERENCES appointments(calendar_event_id),
                    UNIQUE(calendar_event_id, kind)
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_event
                    ON artifacts(calendar_event_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_status
                    ON artifacts(status);
                """
            )

    def upsert_appointment(self, appointment: Appointment) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO appointments (
                    calendar_event_id, calendar_id, etag, title, company,
                    website, founder_name, founder_email, founder_linkedin,
                    industry, strategy_mode, start_at, end_at, status,
                    research_job_id, research_start_at, source_payload_json,
                    last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(calendar_event_id) DO UPDATE SET
                    calendar_id=excluded.calendar_id,
                    etag=excluded.etag,
                    title=excluded.title,
                    company=excluded.company,
                    website=excluded.website,
                    founder_name=excluded.founder_name,
                    founder_email=excluded.founder_email,
                    founder_linkedin=excluded.founder_linkedin,
                    industry=excluded.industry,
                    strategy_mode=excluded.strategy_mode,
                    start_at=excluded.start_at,
                    end_at=excluded.end_at,
                    status=excluded.status,
                    source_payload_json=excluded.source_payload_json,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    appointment.calendar_event_id,
                    appointment.calendar_id,
                    appointment.etag,
                    appointment.title,
                    appointment.company,
                    appointment.website,
                    appointment.founder_name,
                    appointment.founder_email,
                    appointment.founder_linkedin,
                    appointment.industry,
                    appointment.strategy_mode,
                    _iso(appointment.start_at),
                    _iso(appointment.end_at),
                    appointment.status.value,
                    appointment.research_job_id,
                    _iso(appointment.research_start_at),
                    json.dumps(appointment.source_payload, separators=(",", ":")),
                    appointment.last_error,
                    now,
                    now,
                ),
            )

    def get_appointment(self, calendar_event_id: str) -> Appointment | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM appointments WHERE calendar_event_id=?",
                (calendar_event_id,),
            ).fetchone()
        return self._appointment_from_row(row) if row else None

    def list_appointments(self, limit: int = 100) -> list[Appointment]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM appointments ORDER BY start_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._appointment_from_row(row) for row in rows]

    def set_research_job(
        self,
        calendar_event_id: str,
        job_id: str,
        research_start_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE appointments
                SET research_job_id=?, research_start_at=?, status=?,
                    last_error=NULL, updated_at=?
                WHERE calendar_event_id=?
                """,
                (
                    job_id,
                    research_start_at.isoformat(),
                    AppointmentStatus.RESEARCH_SCHEDULED.value,
                    _now(),
                    calendar_event_id,
                ),
            )

    def clear_research_job(self, calendar_event_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE appointments
                SET research_job_id=NULL, research_start_at=NULL, updated_at=?
                WHERE calendar_event_id=?
                """,
                (_now(), calendar_event_id),
            )

    def mark_appointment_status(
        self,
        calendar_event_id: str,
        status: AppointmentStatus,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE appointments
                SET status=?, last_error=?, updated_at=?
                WHERE calendar_event_id=?
                """,
                (status.value, error, _now(), calendar_event_id),
            )

    def cancel_appointment(self, calendar_event_id: str) -> None:
        self.mark_appointment_status(calendar_event_id, AppointmentStatus.CANCELLED)

    def begin_webhook_delivery(self, delivery_id: str, source: str) -> bool:
        """Claim a delivery, allowing a previously failed attempt to retry."""

        now = _now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM webhook_deliveries WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO webhook_deliveries (
                        delivery_id, source, status, attempt_count,
                        first_received_at, updated_at
                    ) VALUES (?, ?, 'processing', 1, ?, ?)
                    """,
                    (delivery_id, source, now, now),
                )
                return True
            if row["status"] != "failed":
                return False
            conn.execute(
                """
                UPDATE webhook_deliveries
                SET status='processing', attempt_count=attempt_count + 1,
                    last_error=NULL, updated_at=?
                WHERE delivery_id=?
                """,
                (now, delivery_id),
            )
            return True

    def finish_webhook_delivery(
        self,
        delivery_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE webhook_deliveries
                SET status=?, last_error=?, updated_at=?
                WHERE delivery_id=?
                """,
                ("completed" if success else "failed", error, _now(), delivery_id),
            )

    def save_recording(self, recording: Recording) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO recordings (
                    recording_id, webhook_id, calendar_event_id, meeting_title,
                    scheduled_start_at, recording_start_at, recording_end_at,
                    external_invitee_emails_json, transcript_path, payload_json,
                    status, analysis_run_id, last_error, received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    calendar_event_id=excluded.calendar_event_id,
                    meeting_title=excluded.meeting_title,
                    scheduled_start_at=excluded.scheduled_start_at,
                    recording_start_at=excluded.recording_start_at,
                    recording_end_at=excluded.recording_end_at,
                    external_invitee_emails_json=excluded.external_invitee_emails_json,
                    transcript_path=excluded.transcript_path,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    analysis_run_id=excluded.analysis_run_id,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    recording.recording_id,
                    recording.webhook_id,
                    recording.calendar_event_id,
                    recording.meeting_title,
                    recording.scheduled_start_at.isoformat(),
                    _iso(recording.recording_start_at),
                    _iso(recording.recording_end_at),
                    json.dumps(recording.external_invitee_emails),
                    recording.transcript_path,
                    json.dumps(recording.payload, separators=(",", ":")),
                    recording.status.value,
                    recording.analysis_run_id,
                    recording.last_error,
                    now,
                    now,
                ),
            )

    def set_recording_analysis_run(self, recording_id: int, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE recordings
                SET analysis_run_id=?, status=?, last_error=NULL, updated_at=?
                WHERE recording_id=?
                """,
                (run_id, RecordingStatus.ANALYSIS_RUNNING.value, _now(), recording_id),
            )

    def mark_recording_status(
        self,
        recording_id: int,
        status: RecordingStatus,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE recordings
                SET status=?, last_error=?, updated_at=?
                WHERE recording_id=?
                """,
                (status.value, error, _now(), recording_id),
            )

    def list_recordings(self, calendar_event_id: str | None = None) -> list[Recording]:
        with self.connect() as conn:
            if calendar_event_id:
                rows = conn.execute(
                    "SELECT * FROM recordings WHERE calendar_event_id=? "
                    "ORDER BY scheduled_start_at DESC",
                    (calendar_event_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM recordings ORDER BY scheduled_start_at DESC LIMIT 200"
                ).fetchall()
        return [self._recording_from_row(row) for row in rows]

    def get_recording(self, recording_id: int) -> Recording | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE recording_id=?",
                (recording_id,),
            ).fetchone()
        return self._recording_from_row(row) if row else None

    def upsert_artifact(self, artifact: Artifact) -> int:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    calendar_event_id, kind, title, status, source_id,
                    file_path, content, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(calendar_event_id, kind) DO UPDATE SET
                    title=excluded.title,
                    status=excluded.status,
                    source_id=excluded.source_id,
                    file_path=excluded.file_path,
                    content=excluded.content,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    artifact.calendar_event_id,
                    artifact.kind,
                    artifact.title,
                    artifact.status.value,
                    artifact.source_id,
                    artifact.file_path,
                    artifact.content,
                    artifact.notes,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM artifacts WHERE calendar_event_id=? AND kind=?",
                (artifact.calendar_event_id, artifact.kind),
            ).fetchone()
        if row is None:
            raise RuntimeError("Artifact insert did not return an id")
        return int(row["id"])

    def get_artifact(self, artifact_id: int) -> Artifact | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self._artifact_from_row(row) if row else None

    def get_artifact_by_kind(self, calendar_event_id: str, kind: str) -> Artifact | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE calendar_event_id=? AND kind=?",
                (calendar_event_id, kind),
            ).fetchone()
        return self._artifact_from_row(row) if row else None

    def list_artifacts(
        self,
        calendar_event_id: str | None = None,
        *,
        statuses: tuple[ArtifactStatus, ...] | None = None,
    ) -> list[Artifact]:
        clauses: list[str] = []
        params: list[str] = []
        if calendar_event_id:
            clauses.append("calendar_event_id=?")
            params.append(calendar_event_id)
        if statuses:
            clauses.append("status IN (%s)" % ",".join("?" for _ in statuses))
            params.extend(item.value for item in statuses)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM artifacts{where} ORDER BY created_at ASC", params
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def claim_artifact_for_processing(
        self,
        artifact_id: int,
        *,
        allowed_statuses: tuple[ArtifactStatus, ...],
        source_id: str = "",
        notes: str = "",
    ) -> bool:
        """Atomically claim a queued/failed artifact so duplicate loops cannot run it."""

        now = _now()
        placeholders = ",".join("?" for _ in allowed_statuses)
        params: list[object] = [
            ArtifactStatus.PROCESSING.value,
            source_id,
            notes,
            now,
            artifact_id,
            *(item.value for item in allowed_statuses),
        ]
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE artifacts
                SET status=?, source_id=?, notes=?, updated_at=?
                WHERE id=? AND status IN ({placeholders})
                """,
                params,
            )
        return cursor.rowcount == 1

    def update_artifact(
        self,
        artifact_id: int,
        *,
        status: ArtifactStatus | None = None,
        source_id: str | None = None,
        file_path: str | None = None,
        content: str | None = None,
        notes: str | None = None,
    ) -> None:
        values: dict[str, object] = {"updated_at": _now()}
        if status is not None:
            values["status"] = status.value
        if source_id is not None:
            values["source_id"] = source_id
        if file_path is not None:
            values["file_path"] = file_path
        if content is not None:
            values["content"] = content
        if notes is not None:
            values["notes"] = notes
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE artifacts SET {assignments} WHERE id=?",
                [*values.values(), artifact_id],
            )

    def match_appointment(
        self,
        meeting_title: str,
        scheduled_start_at: datetime,
        external_invitee_emails: list[str],
        window_minutes: int,
    ) -> Appointment | None:
        window = timedelta(minutes=window_minutes)
        start = (scheduled_start_at - window).isoformat()
        end = (scheduled_start_at + window).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE start_at BETWEEN ? AND ? AND status != ?
                ORDER BY ABS(strftime('%s', start_at) - strftime('%s', ?)) ASC
                """,
                (start, end, AppointmentStatus.CANCELLED.value, scheduled_start_at.isoformat()),
            ).fetchall()

        normalized_title = meeting_title.strip().casefold()
        emails = {email.strip().casefold() for email in external_invitee_emails if email}
        candidates = [self._appointment_from_row(row) for row in rows]
        for appointment in candidates:
            if appointment.title.strip().casefold() == normalized_title:
                return appointment
        for appointment in candidates:
            if appointment.founder_email.strip().casefold() in emails:
                return appointment
        return candidates[0] if len(candidates) == 1 else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, _now()),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else None

    @staticmethod
    def _recording_from_row(row: sqlite3.Row) -> Recording:
        return Recording(
            recording_id=int(row["recording_id"]),
            webhook_id=row["webhook_id"],
            calendar_event_id=row["calendar_event_id"],
            meeting_title=row["meeting_title"],
            scheduled_start_at=_datetime(row["scheduled_start_at"]),  # type: ignore[arg-type]
            recording_start_at=_datetime(row["recording_start_at"]),
            recording_end_at=_datetime(row["recording_end_at"]),
            external_invitee_emails=json.loads(row["external_invitee_emails_json"]),
            transcript_path=row["transcript_path"],
            payload=json.loads(row["payload_json"]),
            status=RecordingStatus(row["status"]),
            analysis_run_id=row["analysis_run_id"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=int(row["id"]),
            calendar_event_id=row["calendar_event_id"],
            kind=row["kind"],
            title=row["title"],
            status=ArtifactStatus(row["status"]),
            source_id=row["source_id"],
            file_path=row["file_path"],
            content=row["content"],
            notes=row["notes"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _appointment_from_row(row: sqlite3.Row) -> Appointment:
        return Appointment(
            calendar_event_id=row["calendar_event_id"],
            calendar_id=row["calendar_id"],
            etag=row["etag"],
            title=row["title"],
            company=row["company"],
            website=row["website"],
            founder_name=row["founder_name"],
            founder_email=row["founder_email"],
            founder_linkedin=row["founder_linkedin"],
            industry=row["industry"],
            strategy_mode=row["strategy_mode"],
            start_at=_datetime(row["start_at"]),  # type: ignore[arg-type]
            end_at=_datetime(row["end_at"]),  # type: ignore[arg-type]
            status=AppointmentStatus(row["status"]),
            research_job_id=row["research_job_id"],
            research_start_at=_datetime(row["research_start_at"]),
            source_payload=json.loads(row["source_payload_json"]),
            last_error=row["last_error"],
        )
