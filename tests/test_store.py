from growth_autopsy.domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus
from growth_autopsy.store import WorkflowStore

from datetime import UTC, datetime


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
        )
    )
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
