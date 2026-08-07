from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from growth_autopsy.api import app, get_store
from growth_autopsy.config import Settings
from growth_autopsy.controller import appointment_detail_payload, dashboard_payload
from growth_autopsy.domain import (
    Artifact,
    ArtifactStatus,
    Appointment,
    AppointmentStatus,
    Recording,
    RecordingStatus,
)
from growth_autopsy.store import WorkflowStore


def _appointment(now: datetime) -> Appointment:
    return Appointment(
        calendar_event_id="event-dashboard",
        calendar_id="primary",
        etag="1",
        title="[GROWTH AUTOPSY] Acme",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="alice@acme.example",
        founder_linkedin="https://linkedin.com/in/alice",
        industry="SaaS",
        strategy_mode="unsure",
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
        status=AppointmentStatus.CONTENT_DRAFTED,
        source_payload={"hangoutLink": "https://meet.google.com/abc-defg-hij"},
    )


def test_dashboard_exposes_operational_metrics_and_sql_timeline(tmp_path) -> None:
    now = datetime.now(UTC)
    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        google_token_file=tmp_path / "google-token.json",
        enable_background_sync=False,
        ai_api_key="configured",
        ai_model="example-model",
        fathom_webhook_secret="configured",
        fathom_api_key="configured",
        notion_api_key="configured",
        notion_parent_page_id="parent",
    )
    settings.google_token_file.write_text("{}", encoding="utf-8")
    store = WorkflowStore(settings.database_path)
    store.initialize()
    appointment = _appointment(now)
    store.upsert_appointment(appointment)
    store.set_research_job(
        appointment.calendar_event_id,
        "local:precall:event-dashboard",
        now + timedelta(hours=1),
    )
    for kind, title in (
        ("precall_research", "Pre-call intelligence"),
        ("growth_autopsy", "Growth Autopsy"),
        ("strategy_decision", "Strategy routing"),
    ):
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind=kind,
                title=title,
                status=ArtifactStatus.READY,
                content="# Evidence-backed draft",
                notes="Ready for Diksha",
            )
        )
    store.save_recording(
        Recording(
            recording_id=42,
            webhook_id="webhook-42",
            calendar_event_id=appointment.calendar_event_id,
            meeting_title=appointment.title,
            scheduled_start_at=appointment.start_at,
            recording_start_at=appointment.start_at,
            recording_end_at=appointment.end_at,
            external_invitee_emails=[appointment.founder_email],
            transcript_path=str(tmp_path / "transcript.md"),
            payload={"share_url": "https://fathom.video/share/example"},
            status=RecordingStatus.ANALYSIS_COMPLETE,
        )
    )

    dashboard = dashboard_payload(settings, store)
    workflow = dashboard["appointments"][0]
    integrations = {item["key"]: item for item in dashboard["system"]["integrations"]}

    assert dashboard["metrics"]["appointments"] == 1
    assert dashboard["metrics"]["awaiting_approval"] == 1
    assert dashboard["metrics"]["routing_decisions"] == 1
    assert workflow["current_stage"]["key"] == "approval"
    assert workflow["next_action"].startswith("Choose case-study-only")
    assert workflow["recording_count"] == 1
    assert integrations["database"]["state"] == "connected"
    assert integrations["calendar"]["state"] == "configured"
    assert integrations["ai"]["state"] == "configured"
    assert dashboard["system"]["database_counts"]["workflow_events"] >= 6

    detail = appointment_detail_payload(settings, store, appointment.calendar_event_id)
    assert detail is not None
    assert detail["recordings"][0]["recording_id"] == 42
    assert detail["recordings"][0]["duration_seconds"] == 3600
    assert any(event["event_type"] == "fathom_received" for event in detail["timeline"])
    assert next(
        item for item in detail["artifacts"] if item["kind"] == "strategy_decision"
    )["action_required"] == "route"


def test_dashboard_detail_returns_none_for_unknown_workflow(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()

    assert appointment_detail_payload(settings, store, "missing") is None
    assert dashboard_payload(settings, store)["system"]["database_counts"] == {
        "appointments": 0,
        "recordings": 0,
        "artifacts": 0,
        "workflow_events": 0,
        "webhook_deliveries": 0,
    }


@pytest.mark.asyncio
async def test_dashboard_is_open_and_delete_endpoint_removes_meeting(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    appointment = _appointment(datetime.now(UTC))
    store.upsert_appointment(appointment)
    app.dependency_overrides[get_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            dashboard = await client.get("/internal/dashboard")
            deleted = await client.delete(
                f"/internal/appointments/{appointment.calendar_event_id}"
            )
            missing = await client.get(
                f"/internal/appointments/{appointment.calendar_event_id}"
            )
    finally:
        app.dependency_overrides.pop(get_store, None)

    assert dashboard.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json()["calendar_event"] == "unchanged"
    assert missing.status_code == 404
    assert store.is_appointment_dismissed(appointment.calendar_event_id)


def test_dashboard_has_delete_control_and_no_access_key_ui() -> None:
    dashboard_dir = Path(__file__).parents[1] / "src/growth_autopsy/dashboard"
    html = (dashboard_dir / "index.html").read_text(encoding="utf-8")
    javascript = (dashboard_dir / "app.js").read_text(encoding="utf-8")

    assert "deleteMeetingButton" in html
    assert "Delete this meeting?" in html
    assert 'method: "DELETE"' in javascript
    assert "Access settings" not in html
    assert "GA_INTERNAL_API_KEY" not in html
    assert "ga_internal_key" not in javascript
