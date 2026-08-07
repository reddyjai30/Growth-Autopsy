from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from growth_autopsy.config import Settings
from growth_autopsy.controller import (
    collect_agent_outputs,
    publish_approved_package,
    queue_postcall_deliverables,
    run_pending_postcall_analysis,
)
from growth_autopsy.domain import (
    Artifact,
    ArtifactStatus,
    Appointment,
    AppointmentStatus,
    Recording,
    RecordingStatus,
)
from growth_autopsy.notion import NotionClient
from growth_autopsy.store import WorkflowStore


def appointment() -> Appointment:
    return Appointment(
        calendar_event_id="event-postcall",
        calendar_id="primary",
        etag="1",
        title="[GROWTH AUTOPSY] Acme",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="alice@acme.example",
        founder_linkedin="",
        industry="Ecommerce",
        strategy_mode="auto",
        start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        status=AppointmentStatus.INTELLIGENCE_READY,
        source_payload={},
    )


class DeliverableHermes:
    async def start_deliverable_run(
        self,
        kind,
        appointment,
        *,
        founder_intelligence_path,
        precall_report_path,
    ):
        assert founder_intelligence_path.endswith("founder-intelligence.md")
        return f"run-{kind}"

    async def get_run(self, run_id):
        return {"status": "completed", "output": f"# Draft from {run_id}"}


class DirectPostcallAI:
    model = "test-model"

    def __init__(self):
        self.deliverables: list[str] = []

    async def synthesize_founder_intelligence(
        self, appointment, fathom_payload, precall_report
    ):
        assert fathom_payload["recording_id"] == 901
        assert "Pre-call" in precall_report
        return (
            "## Meeting Metadata\nVerified call\n"
            "## Strategy-Intent Classification\nFounder requested a strategy.\n"
            "<!-- strategy_intent: strategy_requested -->"
        )

    async def synthesize_postcall_deliverable(
        self, kind, appointment, founder_intelligence, precall_report
    ):
        self.deliverables.append(kind)
        assert "strategy_requested" in founder_intelligence
        return f"## Draft Status\n{kind} draft"


@pytest.mark.asyncio
async def test_strategy_call_queues_and_collects_three_documents(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    item = appointment()
    store.upsert_appointment(item)
    intelligence_path = tmp_path / "founder-intelligence.md"
    intelligence_path.write_text("# FI\n", encoding="utf-8")

    queued = await queue_postcall_deliverables(
        store,
        DeliverableHermes(),  # type: ignore[arg-type]
        item,
        founder_intelligence_path=str(intelligence_path),
        founder_intelligence=(
            "# Founder Intelligence\n\n"
            "<!-- strategy_intent: strategy_requested -->"
        ),
    )

    assert {result["kind"] for result in queued} == {
        "growth_autopsy",
        "strategy_doc",
        "pitch_deck_brief",
    }
    results = await collect_agent_outputs(
        settings,
        store,
        DeliverableHermes(),  # type: ignore[arg-type]
    )
    assert len(results) == 3
    assert all(result["status"] == "ready" for result in results)
    assert store.get_appointment(item.calendar_event_id).status == AppointmentStatus.CONTENT_DRAFTED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_direct_postcall_worker_creates_intelligence_and_three_documents(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    item = appointment()
    item.status = AppointmentStatus.ANALYSIS_RUNNING
    store.upsert_appointment(item)
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=item.calendar_event_id,
            kind="precall_research",
            title="Pre-call intelligence",
            status=ArtifactStatus.READY,
            content="## Pre-call\nEvidence",
        )
    )
    store.save_recording(
        Recording(
            recording_id=901,
            webhook_id="message-901",
            calendar_event_id=item.calendar_event_id,
            meeting_title=item.title,
            scheduled_start_at=item.start_at,
            recording_start_at=item.start_at,
            recording_end_at=item.end_at,
            external_invitee_emails=[item.founder_email],
            transcript_path=str(tmp_path / "901.md"),
            payload={"recording_id": 901, "transcript": []},
            status=RecordingStatus.ANALYSIS_RUNNING,
            analysis_run_id="direct:901",
        )
    )
    ai = DirectPostcallAI()

    results = await run_pending_postcall_analysis(
        settings, store, ai  # type: ignore[arg-type]
    )

    assert results[0]["status"] == "ready"
    assert results[0]["strategy_intent"] == "strategy_requested"
    assert set(ai.deliverables) == {
        "growth_autopsy",
        "strategy_doc",
        "pitch_deck_brief",
    }
    assert store.get_recording(901).status == RecordingStatus.ANALYSIS_COMPLETE  # type: ignore[union-attr]
    assert store.get_appointment(item.calendar_event_id).status == AppointmentStatus.CONTENT_DRAFTED  # type: ignore[union-attr]
    for kind in (
        "founder_intelligence",
        "growth_autopsy",
        "strategy_doc",
        "pitch_deck_brief",
    ):
        assert store.get_artifact_by_kind(item.calendar_event_id, kind).status == ArtifactStatus.READY  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_notion_publish_waits_for_all_approvals_and_is_idempotent(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    item = appointment()
    store.upsert_appointment(item)
    store.set_setting(f"strategy_intent:{item.calendar_event_id}", "strategy_requested")
    for kind in ("growth_autopsy", "strategy_doc", "pitch_deck_brief"):
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=item.calendar_event_id,
                kind=kind,
                title=kind,
                status=ArtifactStatus.APPROVED,
                content=f"# {kind}\nApproved draft",
            )
        )

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/pages"
        assert request.headers["notion-version"] == "2026-03-11"
        return httpx.Response(
            200,
            json={"id": "notion-page-1", "url": "https://notion.so/notion-page-1"},
        )

    notion = NotionClient(
        "secret",
        "parent-page",
        transport=httpx.MockTransport(handler),
    )
    first = await publish_approved_package(
        settings, store, item.calendar_event_id, notion=notion
    )
    second = await publish_approved_package(
        settings, store, item.calendar_event_id, notion=notion
    )

    assert first["status"] == "published"
    assert second["status"] == "already_published"
    assert calls == 1
    assert store.get_appointment(item.calendar_event_id).status == AppointmentStatus.PUBLISHED  # type: ignore[union-attr]
