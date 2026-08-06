from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from .domain import AppointmentStatus, Recording, RecordingStatus
from .hermes import HermesClient
from .store import WorkflowStore


class FathomWebhookError(ValueError):
    pass


def _headers_lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.casefold(): value for key, value in headers.items()}


def verify_fathom_signature(
    secret: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    *,
    now: datetime | None = None,
    tolerance_seconds: int = 300,
) -> str:
    """Verify Fathom's Svix-style signature and return the webhook ID."""

    if not secret:
        raise FathomWebhookError("Fathom webhook secret is not configured")
    normalized = _headers_lower(headers)
    webhook_id = normalized.get("webhook-id", "")
    timestamp_text = normalized.get("webhook-timestamp", "")
    signature_header = normalized.get("webhook-signature", "")
    if not webhook_id or not timestamp_text or not signature_header:
        raise FathomWebhookError("Missing Fathom webhook signature headers")

    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise FathomWebhookError("Invalid Fathom webhook timestamp") from exc

    current = int((now or datetime.now(UTC)).timestamp())
    if abs(current - timestamp) > tolerance_seconds:
        raise FathomWebhookError("Fathom webhook timestamp is outside the replay window")

    encoded_secret = secret.removeprefix("whsec_")
    encoded_secret += "=" * (-len(encoded_secret) % 4)
    try:
        key = base64.b64decode(encoded_secret, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FathomWebhookError("Invalid Fathom webhook secret encoding") from exc

    signed_content = (
        webhook_id.encode("utf-8")
        + b"."
        + timestamp_text.encode("ascii")
        + b"."
        + raw_body
    )
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode("ascii")

    for part in signature_header.split():
        _, separator, signature = part.partition(",")
        candidate = signature if separator else part
        if hmac.compare_digest(candidate, expected):
            return webhook_id
    raise FathomWebhookError("Invalid Fathom webhook signature")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise FathomWebhookError("Fathom timestamps must include a timezone")
    return parsed


def transcript_to_markdown(payload: dict) -> str:
    title = payload.get("meeting_title") or payload.get("title") or "Fathom meeting"
    recording_id = payload.get("recording_id", "unknown")
    lines = [
        f"# {title}",
        "",
        f"Recording ID: {recording_id}",
        f"Scheduled start: {payload.get('scheduled_start_time', '')}",
        "",
        "## Transcript",
        "",
    ]
    transcript = payload.get("transcript") or []
    if not transcript:
        lines.append("[Transcript was not included in the webhook payload.]")
    for item in transcript:
        speaker = (item.get("speaker") or {}).get("display_name") or "Unknown speaker"
        timestamp = item.get("timestamp") or ""
        text = str(item.get("text") or "").strip()
        lines.append(f"**{timestamp} — {speaker}:** {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@dataclass(slots=True)
class FathomIngestionResult:
    status: str
    webhook_id: str
    recording_id: int | None = None
    calendar_event_id: str | None = None
    analysis_run_id: str | None = None


class FathomIngestionService:
    def __init__(
        self,
        store: WorkflowStore,
        hermes: HermesClient,
        *,
        webhook_secret: str,
        transcript_dir: Path,
        match_window_minutes: int,
    ):
        self.store = store
        self.hermes = hermes
        self.webhook_secret = webhook_secret
        self.transcript_dir = transcript_dir
        self.match_window_minutes = match_window_minutes

    async def ingest(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> FathomIngestionResult:
        webhook_id = verify_fathom_signature(
            self.webhook_secret,
            headers,
            raw_body,
        )
        if not self.store.begin_webhook_delivery(webhook_id, "fathom"):
            return FathomIngestionResult(status="duplicate", webhook_id=webhook_id)

        recording_id: int | None = None
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise FathomWebhookError("Fathom webhook body must be a JSON object")
            recording_id = int(payload["recording_id"])
            scheduled_start = _parse_datetime(payload.get("scheduled_start_time"))
            if scheduled_start is None:
                raise FathomWebhookError("Fathom payload is missing scheduled_start_time")

            existing_recording = self.store.get_recording(recording_id)
            if existing_recording and existing_recording.analysis_run_id:
                self.store.finish_webhook_delivery(webhook_id, success=True)
                return FathomIngestionResult(
                    status="duplicate_recording",
                    webhook_id=webhook_id,
                    recording_id=recording_id,
                    calendar_event_id=existing_recording.calendar_event_id,
                    analysis_run_id=existing_recording.analysis_run_id,
                )

            invitees = payload.get("calendar_invitees") or []
            external_emails = [
                str(invitee.get("email") or "")
                for invitee in invitees
                if invitee.get("is_external") and invitee.get("email")
            ]
            meeting_title = str(payload.get("meeting_title") or payload.get("title") or "")
            appointment = self.store.match_appointment(
                meeting_title,
                scheduled_start,
                external_emails,
                self.match_window_minutes,
            )

            self.transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = (self.transcript_dir / f"{recording_id}.md").resolve()
            payload_path = (self.transcript_dir / f"{recording_id}.json").resolve()
            transcript_path.write_text(transcript_to_markdown(payload), encoding="utf-8")
            payload_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            recording = Recording(
                recording_id=recording_id,
                webhook_id=webhook_id,
                calendar_event_id=appointment.calendar_event_id if appointment else None,
                meeting_title=meeting_title,
                scheduled_start_at=scheduled_start,
                recording_start_at=_parse_datetime(payload.get("recording_start_time")),
                recording_end_at=_parse_datetime(payload.get("recording_end_time")),
                external_invitee_emails=external_emails,
                transcript_path=str(transcript_path),
                payload=payload,
                status=RecordingStatus.RECEIVED if appointment else RecordingStatus.UNMATCHED,
            )
            self.store.save_recording(recording)

            if appointment is None:
                self.store.finish_webhook_delivery(webhook_id, success=True)
                return FathomIngestionResult(
                    status="unmatched",
                    webhook_id=webhook_id,
                    recording_id=recording_id,
                )

            self.store.mark_appointment_status(
                appointment.calendar_event_id,
                AppointmentStatus.TRANSCRIPT_READY,
            )
            run_id = await self.hermes.start_postcall_run(
                appointment,
                recording_id,
                str(transcript_path),
            )
            self.store.set_recording_analysis_run(recording_id, run_id)
            self.store.mark_appointment_status(
                appointment.calendar_event_id,
                AppointmentStatus.ANALYSIS_RUNNING,
            )
            self.store.finish_webhook_delivery(webhook_id, success=True)
            return FathomIngestionResult(
                status="analysis_started",
                webhook_id=webhook_id,
                recording_id=recording_id,
                calendar_event_id=appointment.calendar_event_id,
                analysis_run_id=run_id,
            )
        except Exception as exc:
            self.store.finish_webhook_delivery(webhook_id, success=False, error=str(exc)[:1000])
            if recording_id is not None:
                self.store.mark_recording_status(
                    recording_id,
                    RecordingStatus.FAILED,
                    str(exc)[:1000],
                )
            raise
