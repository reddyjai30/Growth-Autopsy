from growth_autopsy.domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus
from growth_autopsy.store import WorkflowStore

from datetime import UTC, datetime, timedelta, timezone


def test_failed_webhook_delivery_can_retry(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()

    assert store.begin_webhook_delivery("msg-1", "fathom") is True
    assert store.begin_webhook_delivery("msg-1", "fathom") is False


def test_artifact_claim_is_atomic(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_appointment(
        Appointment(
            calendar_event_id="event-1",
            calendar_id="primary",
            etag="1",
            title="Call",
            company="Acme",
            website="https://acme.example",
            founder_name="Alice",
            founder_email="",
            founder_linkedin="",
            industry="",
            strategy_mode="auto",
            start_at=datetime(2026, 8, 6, 10, tzinfo=UTC),
            end_at=datetime(2026, 8, 6, 11, tzinfo=UTC),
            status=AppointmentStatus.RESEARCH_SCHEDULED,
            source_payload={},
            meeting_agenda="Review growth priorities",
        )
    )
    assert store.get_appointment("event-1").meeting_agenda == "Review growth priorities"
    artifact_id = store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id="event-1",
            kind="precall_research",
            title="Research",
            status=ArtifactStatus.SCHEDULED,
        )
    )

    assert store.claim_artifact_for_processing(
        artifact_id,
        allowed_statuses=(ArtifactStatus.SCHEDULED,),
        source_id="worker-a",
    )
    assert not store.claim_artifact_for_processing(
        artifact_id,
        allowed_statuses=(ArtifactStatus.SCHEDULED,),
        source_id="worker-b",
    )

    store.finish_webhook_delivery("msg-1", success=False, error="temporary")
    assert store.begin_webhook_delivery("msg-1", "fathom") is True

    store.finish_webhook_delivery("msg-1", success=True)
    assert store.begin_webhook_delivery("msg-1", "fathom") is False


def test_calendar_to_fathom_match_normalizes_timezone_offsets(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    india = timezone(timedelta(hours=5, minutes=30))
    store.upsert_appointment(
        Appointment(
            calendar_event_id="event-timezone",
            calendar_id="primary",
            etag="1",
            title="[GROWTH AUTOPSY] Acme",
            company="Acme",
            website="https://acme.example",
            founder_name="Alice",
            founder_email="alice@acme.example",
            founder_linkedin="",
            industry="",
            strategy_mode="auto",
            start_at=datetime(2026, 8, 20, 15, 0, tzinfo=india),
            end_at=datetime(2026, 8, 20, 16, 0, tzinfo=india),
            status=AppointmentStatus.BOOKED,
            source_payload={},
        )
    )

    matched = store.match_appointment(
        "[GROWTH AUTOPSY] Acme",
        datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        ["alice@acme.example"],
        20,
    )

    assert matched is not None
    assert matched.calendar_event_id == "event-timezone"


def test_delete_appointment_removes_workflow_and_blocks_calendar_reimport(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()
    appointment = Appointment(
        calendar_event_id="event-delete",
        calendar_id="primary",
        etag="1",
        title="[GROWTH AUTOPSY] Delete Me",
        company="Delete Me",
        website="https://delete.example",
        founder_name="Alice",
        founder_email="",
        founder_linkedin="",
        industry="",
        strategy_mode="auto",
        start_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 11, tzinfo=UTC),
        status=AppointmentStatus.BOOKED,
        source_payload={},
    )
    assert store.upsert_appointment(appointment) is True
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=appointment.calendar_event_id,
            kind="precall_research",
            title="Research",
            status=ArtifactStatus.READY,
        )
    )

    assert store.delete_appointment(appointment.calendar_event_id) is True
    assert store.get_appointment(appointment.calendar_event_id) is None
    assert store.list_artifacts(appointment.calendar_event_id) == []
    assert store.list_workflow_events(appointment.calendar_event_id) == []
    assert store.is_appointment_dismissed(appointment.calendar_event_id) is True
    assert store.upsert_appointment(appointment) is False
    assert store.get_appointment(appointment.calendar_event_id) is None
    assert store.delete_appointment(appointment.calendar_event_id) is False
