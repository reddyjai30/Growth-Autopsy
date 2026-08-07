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
    return value.astimezone(UTC).isoformat() if value is not None else None


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
                    meeting_agenda TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS dismissed_appointments (
                    calendar_event_id TEXT PRIMARY KEY,
                    dismissed_at TEXT NOT NULL
                );

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

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calendar_event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT 'info',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(calendar_event_id)
                        REFERENCES appointments(calendar_event_id)
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_events_event_created
                    ON workflow_events(calendar_event_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_events_created
                    ON workflow_events(created_at DESC);
                """
            )
            appointment_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(appointments)")
            }
            if "meeting_agenda" not in appointment_columns:
                conn.execute(
                    "ALTER TABLE appointments ADD COLUMN meeting_agenda TEXT NOT NULL DEFAULT ''"
                )
            # Earlier builds preserved source offsets. Normalize legacy rows so
            # SQLite range queries compare the same instant across Calendar and Fathom.
            for table, primary_key, columns in (
                ("appointments", "calendar_event_id", ("start_at", "end_at", "research_start_at")),
                (
                    "recordings",
                    "recording_id",
                    ("scheduled_start_at", "recording_start_at", "recording_end_at"),
                ),
            ):
                rows = conn.execute(
                    f"SELECT {primary_key}, {', '.join(columns)} FROM {table}"
                ).fetchall()
                for row in rows:
                    updates = {
                        column: _iso(_datetime(row[column]))
                        for column in columns
                        if row[column]
                    }
                    if not updates:
                        continue
                    assignments = ", ".join(f"{column}=?" for column in updates)
                    conn.execute(
                        f"UPDATE {table} SET {assignments} WHERE {primary_key}=?",
                        [*updates.values(), row[primary_key]],
                    )

    def upsert_appointment(self, appointment: Appointment) -> bool:
        now = _now()
        with self.connect() as conn:
            dismissed = conn.execute(
                "SELECT 1 FROM dismissed_appointments WHERE calendar_event_id=?",
                (appointment.calendar_event_id,),
            ).fetchone()
            if dismissed:
                return False
            previous = conn.execute(
                "SELECT etag, status, start_at FROM appointments WHERE calendar_event_id=?",
                (appointment.calendar_event_id,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO appointments (
                    calendar_event_id, calendar_id, etag, title, company,
                    website, founder_name, founder_email, founder_linkedin,
                    industry, strategy_mode, meeting_agenda, start_at, end_at, status,
                    research_job_id, research_start_at, source_payload_json,
                    last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    meeting_agenda=excluded.meeting_agenda,
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
                    appointment.meeting_agenda,
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
            if previous is None:
                self._insert_event(
                    conn,
                    appointment.calendar_event_id,
                    "booking_created",
                    "Calendar booking added",
                    f"{appointment.company} discovery call entered the pipeline.",
                    payload={"status": appointment.status.value},
                )
            elif previous["etag"] != appointment.etag:
                moved = previous["start_at"] != _iso(appointment.start_at)
                self._insert_event(
                    conn,
                    appointment.calendar_event_id,
                    "calendar_updated",
                    "Calendar booking updated",
                    "Call time changed." if moved else "Calendar details changed.",
                    payload={"rescheduled": moved},
                )
        return True

    def is_appointment_dismissed(self, calendar_event_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM dismissed_appointments WHERE calendar_event_id=?",
                (calendar_event_id,),
            ).fetchone()
        return row is not None

    def delete_appointment(self, calendar_event_id: str) -> bool:
        """Delete one workflow and suppress re-import of its Calendar event."""

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM appointments WHERE calendar_event_id=?",
                (calendar_event_id,),
            ).fetchone()
            if exists is None:
                return False
            conn.execute(
                "INSERT INTO dismissed_appointments (calendar_event_id, dismissed_at) "
                "VALUES (?, ?) ON CONFLICT(calendar_event_id) DO UPDATE SET "
                "dismissed_at=excluded.dismissed_at",
                (calendar_event_id, _now()),
            )
            conn.execute(
                "DELETE FROM workflow_events WHERE calendar_event_id=?",
                (calendar_event_id,),
            )
            conn.execute(
                "DELETE FROM artifacts WHERE calendar_event_id=?",
                (calendar_event_id,),
            )
            conn.execute(
                "DELETE FROM recordings WHERE calendar_event_id=?",
                (calendar_event_id,),
            )
            conn.execute(
                "DELETE FROM appointments WHERE calendar_event_id=?",
                (calendar_event_id,),
            )
        return True

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
                    _iso(research_start_at),
                    AppointmentStatus.RESEARCH_SCHEDULED.value,
                    _now(),
                    calendar_event_id,
                ),
            )
            self._insert_event(
                conn,
                calendar_event_id,
                "research_scheduled",
                "Pre-call research scheduled",
                f"Evidence collection is scheduled for {_iso(research_start_at)}.",
                payload={"job_id": job_id},
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
            previous = conn.execute(
                "SELECT status, last_error FROM appointments WHERE calendar_event_id=?",
                (calendar_event_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE appointments
                SET status=?, last_error=?, updated_at=?
                WHERE calendar_event_id=?
                """,
                (status.value, error, _now(), calendar_event_id),
            )
            if previous and (
                previous["status"] != status.value
                or (error and previous["last_error"] != error)
            ):
                self._insert_event(
                    conn,
                    calendar_event_id,
                    "status_changed",
                    f"Workflow moved to {status.value.replace('_', ' ').title()}",
                    error or f"Previous status: {previous['status']}",
                    severity="error" if status == AppointmentStatus.FAILED else "info",
                    payload={"from": previous["status"], "to": status.value},
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
            previous = conn.execute(
                "SELECT recording_id FROM recordings WHERE recording_id=?",
                (recording.recording_id,),
            ).fetchone()
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
                    _iso(recording.scheduled_start_at),
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
            if previous is None and recording.calendar_event_id:
                self._insert_event(
                    conn,
                    recording.calendar_event_id,
                    "fathom_received",
                    "Fathom transcript received",
                    f"Recording {recording.recording_id} matched to this Calendar call.",
                    payload={"recording_id": recording.recording_id},
                )

    def set_recording_analysis_run(self, recording_id: int, run_id: str) -> None:
        with self.connect() as conn:
            recording = conn.execute(
                "SELECT calendar_event_id FROM recordings WHERE recording_id=?",
                (recording_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE recordings
                SET analysis_run_id=?, status=?, last_error=NULL, updated_at=?
                WHERE recording_id=?
                """,
                (run_id, RecordingStatus.ANALYSIS_RUNNING.value, _now(), recording_id),
            )
            if recording and recording["calendar_event_id"]:
                self._insert_event(
                    conn,
                    recording["calendar_event_id"],
                    "analysis_started",
                    "Founder Intelligence started",
                    "Hermes is analyzing the verified transcript.",
                    payload={"recording_id": recording_id, "run_id": run_id},
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
            previous = conn.execute(
                "SELECT id, status FROM artifacts WHERE calendar_event_id=? AND kind=?",
                (artifact.calendar_event_id, artifact.kind),
            ).fetchone()
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
            if previous is None or previous["status"] != artifact.status.value:
                action = "created" if previous is None else "updated"
                self._insert_event(
                    conn,
                    artifact.calendar_event_id,
                    "artifact_status",
                    f"{artifact.title} {action}",
                    f"Artifact status: {artifact.status.value.replace('_', ' ').title()}.",
                    severity="error" if artifact.status == ArtifactStatus.FAILED else "info",
                    payload={
                        "artifact_id": int(row["id"]) if row else None,
                        "kind": artifact.kind,
                        "status": artifact.status.value,
                    },
                )
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
            artifact = conn.execute(
                "SELECT calendar_event_id, kind, title, status FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
            cursor = conn.execute(
                f"""
                UPDATE artifacts
                SET status=?, source_id=?, notes=?, updated_at=?
                WHERE id=? AND status IN ({placeholders})
                """,
                params,
            )
            if cursor.rowcount == 1 and artifact:
                self._insert_event(
                    conn,
                    artifact["calendar_event_id"],
                    "artifact_processing",
                    f"{artifact['title']} started",
                    f"Previous status: {artifact['status']}.",
                    payload={"artifact_id": artifact_id, "kind": artifact["kind"]},
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
            previous = conn.execute(
                "SELECT calendar_event_id, kind, title, status FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
            conn.execute(
                f"UPDATE artifacts SET {assignments} WHERE id=?",
                [*values.values(), artifact_id],
            )
            if previous and status is not None and previous["status"] != status.value:
                self._insert_event(
                    conn,
                    previous["calendar_event_id"],
                    "artifact_status",
                    f"{previous['title']} is {status.value.replace('_', ' ').title()}",
                    notes or f"Previous status: {previous['status']}.",
                    severity="error" if status == ArtifactStatus.FAILED else "info",
                    payload={
                        "artifact_id": artifact_id,
                        "kind": previous["kind"],
                        "from": previous["status"],
                        "to": status.value,
                    },
                )

    def match_appointment(
        self,
        meeting_title: str,
        scheduled_start_at: datetime,
        external_invitee_emails: list[str],
        window_minutes: int,
    ) -> Appointment | None:
        window = timedelta(minutes=window_minutes)
        scheduled_utc = scheduled_start_at.astimezone(UTC)
        start = (scheduled_utc - window).isoformat()
        end = (scheduled_utc + window).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE start_at BETWEEN ? AND ? AND status != ?
                ORDER BY ABS(strftime('%s', start_at) - strftime('%s', ?)) ASC
                """,
                (start, end, AppointmentStatus.CANCELLED.value, scheduled_utc.isoformat()),
            ).fetchall()

        normalized_title = meeting_title.strip().casefold()
        emails = {email.strip().casefold() for email in external_invitee_emails if email}
        scored: list[tuple[int, float, Appointment]] = []
        for row in rows:
            appointment = self._appointment_from_row(row)
            delta = abs((appointment.start_at - scheduled_start_at).total_seconds())
            score = 0
            if normalized_title and appointment.title.strip().casefold() == normalized_title:
                score += 5
            if appointment.founder_email.strip().casefold() in emails:
                score += 5
            if delta <= 120:
                score += 3
            elif delta <= window.total_seconds():
                score += 1
            scored.append((score, delta, appointment))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            return None
        best_score = scored[0][0]
        tied = len(scored) > 1 and scored[1][0] == best_score
        # A matching title/email is decisive. An exact scheduled time is accepted
        # only when it identifies one unambiguous Calendar event.
        if best_score >= 5 or (best_score >= 3 and not tied and len(scored) == 1):
            return scored[0][2]
        return None

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

    def add_workflow_event(
        self,
        calendar_event_id: str,
        event_type: str,
        title: str,
        detail: str = "",
        *,
        severity: str = "info",
        payload: dict | None = None,
    ) -> None:
        with self.connect() as conn:
            self._insert_event(
                conn,
                calendar_event_id,
                event_type,
                title,
                detail,
                severity=severity,
                payload=payload,
            )

    def list_workflow_events(
        self,
        calendar_event_id: str | None = None,
        *,
        limit: int = 100,
    ) -> list[dict]:
        safe_limit = max(1, min(limit, 500))
        with self.connect() as conn:
            if calendar_event_id:
                rows = conn.execute(
                    "SELECT * FROM workflow_events WHERE calendar_event_id=? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (calendar_event_id, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_events ORDER BY created_at DESC, id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "calendar_event_id": row["calendar_event_id"],
                "event_type": row["event_type"],
                "title": row["title"],
                "detail": row["detail"],
                "severity": row["severity"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def database_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "appointments",
                    "recordings",
                    "artifacts",
                    "workflow_events",
                    "webhook_deliveries",
                )
            }

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        calendar_event_id: str,
        event_type: str,
        title: str,
        detail: str = "",
        *,
        severity: str = "info",
        payload: dict | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO workflow_events (
                calendar_event_id, event_type, title, detail,
                severity, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calendar_event_id,
                event_type[:100],
                title[:300],
                detail[:2000],
                severity if severity in {"info", "warning", "error", "success"} else "info",
                json.dumps(payload or {}, separators=(",", ":")),
                _now(),
            ),
        )

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
            meeting_agenda=row["meeting_agenda"],
            research_job_id=row["research_job_id"],
            research_start_at=_datetime(row["research_start_at"]),
            source_payload=json.loads(row["source_payload_json"]),
            last_error=row["last_error"],
        )
