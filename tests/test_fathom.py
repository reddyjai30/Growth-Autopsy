from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
import httpx

from growth_autopsy.domain import Appointment, AppointmentStatus
from growth_autopsy.fathom import (
    FathomIngestionService,
    FathomWebhookError,
    verify_fathom_signature,
)
from growth_autopsy.store import WorkflowStore


RAW_SECRET = b"test-fathom-secret-32-bytes-long!"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(RAW_SECRET).decode()


def signed_headers(webhook_id: str, body: bytes, when: datetime) -> dict[str, str]:
    timestamp = str(int(when.timestamp()))
    signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + body
    signature = base64.b64encode(
        hmac.new(RAW_SECRET, signed, hashlib.sha256).digest()
    ).decode()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{signature}",
    }


def test_verify_fathom_signature() -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    body = b'{"recording_id":123}'
    headers = signed_headers("msg-1", body, now)

    assert verify_fathom_signature(WEBHOOK_SECRET, headers, body, now=now) == "msg-1"


def test_verify_fathom_signature_rejects_replay() -> None:
    signed_at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    received_at = datetime(2026, 8, 5, 10, 6, tzinfo=UTC)
    body = b"{}"
    headers = signed_headers("msg-old", body, signed_at)

    with pytest.raises(FathomWebhookError, match="replay window"):
        verify_fathom_signature(WEBHOOK_SECRET, headers, body, now=received_at)


@pytest.mark.asyncio
async def test_fathom_ingestion_matches_calendar_and_queues_direct_analysis(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_appointment(
        Appointment(
            calendar_event_id="event-123",
            calendar_id="primary",
            etag="etag-1",
            title="[GROWTH AUTOPSY] Acme – Alice Founder",
            company="Acme",
            website="https://acme.example",
            founder_name="Alice Founder",
            founder_email="alice@acme.example",
            founder_linkedin="",
            industry="Ecommerce",
            strategy_mode="auto",
            start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
            end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
            status=AppointmentStatus.BOOKED,
            source_payload={},
        )
    )
    payload = {
        "recording_id": 777,
        "meeting_title": "[GROWTH AUTOPSY] Acme – Alice Founder",
        "scheduled_start_time": "2026-08-20T09:30:00Z",
        "recording_start_time": "2026-08-20T09:31:00Z",
        "recording_end_time": "2026-08-20T10:20:00Z",
        "calendar_invitees": [
            {"name": "Alice Founder", "email": "alice@acme.example", "is_external": True}
        ],
        "transcript": [
            {
                "speaker": {"display_name": "Alice Founder"},
                "timestamp": "00:01:10",
                "text": "We need help improving retention.",
            }
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    now = datetime.now(UTC)
    headers = signed_headers("msg-777", body, now)
    service = FathomIngestionService(
        store,
        webhook_secret=WEBHOOK_SECRET,
        transcript_dir=tmp_path / "transcripts",
        match_window_minutes=20,
    )

    result = await service.ingest(body, headers)

    assert result.status == "analysis_queued"
    assert result.calendar_event_id == "event-123"
    assert result.analysis_run_id == "direct:777"
    recording = store.get_recording(777)
    assert recording is not None
    assert recording.analysis_run_id == "direct:777"
    assert (tmp_path / "transcripts" / "777.md").exists()

    duplicate = await service.ingest(body, headers)
    assert duplicate.status == "duplicate"


@pytest.mark.asyncio
async def test_fathom_fetches_transcript_when_webhook_omits_it(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_appointment(
        Appointment(
            calendar_event_id="event-fallback",
            calendar_id="primary",
            etag="etag-1",
            title="[GROWTH AUTOPSY] Acme",
            company="Acme",
            website="https://acme.example",
            founder_name="Alice",
            founder_email="alice@acme.example",
            founder_linkedin="",
            industry="",
            strategy_mode="case_study_only",
            start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
            end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
            status=AppointmentStatus.BOOKED,
            source_payload={},
        )
    )
    payload = {
        "recording_id": 778,
        "meeting_title": "[GROWTH AUTOPSY] Acme",
        "scheduled_start_time": "2026-08-20T09:30:00Z",
        "calendar_invitees": [
            {"email": "alice@acme.example", "is_external": True}
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    now = datetime.now(UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/external/v1/recordings/778/transcript"
        assert request.headers["x-api-key"] == "fathom-key"
        return httpx.Response(
            200,
            json={
                "transcript": [
                    {
                        "speaker": {"display_name": "Alice"},
                        "timestamp": "00:00:05",
                        "text": "We need a retention plan.",
                    }
                ]
            },
        )

    service = FathomIngestionService(
        store,
        webhook_secret=WEBHOOK_SECRET,
        transcript_dir=tmp_path / "transcripts",
        match_window_minutes=20,
        api_key="fathom-key",
        transport=httpx.MockTransport(handler),
    )
    result = await service.ingest(body, signed_headers("msg-778", body, now))

    assert result.status == "analysis_queued"
    assert "retention plan" in (tmp_path / "transcripts" / "778.md").read_text()
