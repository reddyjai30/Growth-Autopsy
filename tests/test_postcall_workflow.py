from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from growth_autopsy.api import DecisionRequest, artifact_decision
from growth_autopsy.config import Settings
from growth_autopsy.controller import (
    collect_agent_outputs,
    generate_dependent_deliverable_direct,
    publish_approved_package,
    queue_postcall_deliverables,
    revise_postcall_artifact_direct,
    retry_postcall_artifact_direct,
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
        if run_id == "run-growth_autopsy":
            output = """## 1 · Brand Snapshot
Acme serves growing teams.
It sells a software subscription.
Its buyers are operations leaders.
The founder leads sales.
## 2 · Founder Story
Alice built Acme after seeing the problem directly.
## 3 · Growth Timeline
The founder described an early customer milestone.
## 4 · Business Model Breakdown
The business uses subscriptions; pricing was not provided.
## 5 · Growth Operating System
### Traffic
Gap — Not established.
### Conversion
Active — Founder-led sales.
### Retention
Gap — Not established.
### Expansion
Gap — Not established.
## 7 · What Acme Did Really Well
The founder built a clear offer from direct customer understanding.
## 8 · Key Challenges / Bottlenecks
Founder-led sales creates a structural capacity ceiling.
## 9 · Opportunities They're Missing
There is an untapped repeatable sales motion.
## 10 · Strategic Observations
### Trust
Our reading is that founder authority carries the current trust burden.
## 11 · What Marketing Mosaic Suggests
### Codify the motion
**Observation:** Sales is founder-led.
**Evidence:** The founder described handling sales.
**Impact:** Capacity remains tied to founder time.
**Unlock:** Document the repeatable decision path.
## 12 · Biggest Growth Lever
If we owned this company for the next 90 days, we would focus on codifying the sales motion.
## 13 · What Other Founders Can Learn
1. Direct customer knowledge is strategic evidence.
2. Founder-led selling can reveal the future playbook.
3. Repeatability matters before scale.
## 14 · Expert Summary
| Dimension | Reading |
| --- | --- |
| Brand Maturity | Emerging |
| Biggest Growth Lever | Codified sales |
| Biggest Risk if Unchanged | Founder capacity |
| Next Phase Recommendation | Document and test |

Acme has the customer understanding needed for its next phase. The ingredients are there.
"""
        else:
            output = """## 1 · The Problem, In Their Exact Words
> “Sales depends entirely on me.” — Alice, 00:20
## 2 · The Problem Beneath the Problem
### Surface Cost
Founder time is consumed.
### Compounding Cost
Inputs were not provided for arithmetic.
### Invisible Cost
Delegation remains hard.
### Gut-Punch Question
How many founder hours could a repeatable system return?
## 3 · Why This Problem Exists
This is not a founder-discipline problem; it is a missing-system problem.
## 4 · What We've Seen
Founder-led selling often contains the raw material for a playbook.
## 5 · The Destination
### What Your Week Looks Like After This
The founder reviews a qualified pipeline.
| Today | Day 90 |
| --- | --- |
| Founder holds the process | Team follows a playbook |
## 6A · The Strategy
### Capture
Document the working motion.
### Codify
Turn it into stages and decisions.
### Transfer
Test delegation.
## 6B · The Execution Gap
You could build this yourself. Doing so requires capture, tooling and test cycles.
## 6C · The Vehicle
| Strategy Phase | MMS Deliverable |
| --- | --- |
| Capture | Sales Playbook discovery |
| Codify | Sales Playbook system |
| Transfer | Sales Playbook rollout |
| System Does | You Do |
| --- | --- |
| Documents and tests | Approve and review |
## 7 · Why Us, Why Now
The Growth Autopsy is the proof of work; commercial terms require confirmation.
## 8 · The Maths
Founder-hour inputs were not provided. [DIKSHA INPUT REQUIRED]
## 9 · The Investment
What would change if sales no longer depended on one calendar?

**Option A:** [DIKSHA INPUT REQUIRED]

**Option B:** [DIKSHA INPUT REQUIRED]

Choose the 1st or 15th start date after Diksha confirms availability.
"""
        return {"status": "completed", "output": output}


class DirectPostcallAI:
    model = "test-model"

    def __init__(self):
        self.deliverables: list[str] = []
        self.revision_parent = ""

    async def synthesize_founder_intelligence(
        self, appointment, fathom_payload, precall_report
    ):
        assert fathom_payload["recording_id"] == 901
        assert "Pre-call" in precall_report
        return (
            "## Meeting Metadata\nVerified call\n"
            "## Strategy-Intent Classification\nFounder requested a strategy.\n"
            "<!-- strategy_intent: strategy_requested -->\n"
            "<!-- service_lane: sales_playbook -->"
        )

    async def synthesize_postcall_deliverable(
        self,
        kind,
        appointment,
        founder_intelligence,
        precall_report,
        *,
        source_document="",
        service_lane="unsure",
    ):
        self.deliverables.append(kind)
        assert "strategy_requested" in founder_intelligence
        if kind in {"linkedin_post", "pitch_deck_brief"}:
            assert source_document
        assert service_lane == "sales_playbook"
        return f"## Draft Status\n{kind} draft"

    async def revise_postcall_deliverable(
        self,
        kind,
        current_draft,
        revision_notes,
        *,
        brand,
        has_external_research,
        service_lane,
        source_document="",
    ):
        assert brand == "Acme"
        assert service_lane == "sales_playbook"
        self.revision_parent = source_document
        return current_draft + f"\n\nRevision applied: {revision_notes}"


@pytest.mark.asyncio
async def test_strategy_call_queues_parent_documents_and_schedules_dependents(tmp_path) -> None:
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
            "<!-- strategy_intent: strategy_requested -->\n"
            "<!-- service_lane: sales_playbook -->"
        ),
    )

    assert {result["kind"] for result in queued} == {
        "growth_autopsy",
        "strategy_doc",
    }
    results = await collect_agent_outputs(
        settings,
        store,
        DeliverableHermes(),  # type: ignore[arg-type]
    )
    assert len(results) == 2
    assert all(result["status"] == "ready" for result in results)
    assert store.get_artifact_by_kind(
        item.calendar_event_id, "linkedin_post"
    ).status == ArtifactStatus.SCHEDULED  # type: ignore[union-attr]
    assert store.get_artifact_by_kind(
        item.calendar_event_id, "pitch_deck_brief"
    ).status == ArtifactStatus.SCHEDULED  # type: ignore[union-attr]
    assert store.get_appointment(item.calendar_event_id).status == AppointmentStatus.CONTENT_DRAFTED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_direct_postcall_worker_creates_parent_documents_then_approval_children(
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
    }
    assert store.get_recording(901).status == RecordingStatus.ANALYSIS_COMPLETE  # type: ignore[union-attr]
    assert store.get_appointment(item.calendar_event_id).status == AppointmentStatus.CONTENT_DRAFTED  # type: ignore[union-attr]
    for kind in (
        "founder_intelligence",
        "growth_autopsy",
        "strategy_doc",
    ):
        assert store.get_artifact_by_kind(item.calendar_event_id, kind).status == ArtifactStatus.READY  # type: ignore[union-attr]
    for kind in ("linkedin_post", "pitch_deck_brief"):
        assert store.get_artifact_by_kind(item.calendar_event_id, kind).status == ArtifactStatus.SCHEDULED  # type: ignore[union-attr]

    for parent_kind in ("growth_autopsy", "strategy_doc"):
        parent = store.get_artifact_by_kind(item.calendar_event_id, parent_kind)
        assert parent is not None and parent.id is not None
        store.update_artifact(parent.id, status=ArtifactStatus.APPROVED)
        parent = store.get_artifact(parent.id)
        assert parent is not None
        child = await generate_dependent_deliverable_direct(
            settings, store, ai, parent  # type: ignore[arg-type]
        )
        assert child is not None and child["status"] == "ready"

    assert set(ai.deliverables) == {
        "growth_autopsy",
        "strategy_doc",
        "linkedin_post",
        "pitch_deck_brief",
    }

    report = store.get_artifact_by_kind(item.calendar_event_id, "growth_autopsy")
    post = store.get_artifact_by_kind(item.calendar_event_id, "linkedin_post")
    assert report is not None and report.id is not None
    assert post is not None and post.id is not None
    store.update_artifact(post.id, status=ArtifactStatus.APPROVED)
    await revise_postcall_artifact_direct(
        settings,
        store,
        ai,  # type: ignore[arg-type]
        report,
        "Make the founder story more concise",
    )
    invalidated = store.get_artifact(post.id)
    assert invalidated is not None
    assert invalidated.status == ArtifactStatus.SCHEDULED
    assert invalidated.content == ""


@pytest.mark.asyncio
async def test_notion_publish_waits_for_all_approvals_and_is_idempotent(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    item = appointment()
    store.upsert_appointment(item)
    store.set_setting(f"strategy_intent:{item.calendar_event_id}", "strategy_requested")
    for kind in (
        "growth_autopsy",
        "linkedin_post",
        "strategy_doc",
        "pitch_deck_brief",
    ):
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


@pytest.mark.asyncio
async def test_approval_endpoint_generates_child_from_approved_parent(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", shared_workdir=tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    item = appointment()
    store.upsert_appointment(item)
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=item.calendar_event_id,
            kind="founder_intelligence",
            title="Founder Intelligence",
            status=ArtifactStatus.READY,
            content=(
                "Internal intelligence\n"
                "<!-- strategy_intent: strategy_requested -->\n"
                "<!-- service_lane: sales_playbook -->"
            ),
        )
    )
    report_id = store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=item.calendar_event_id,
            kind="growth_autopsy",
            title="Growth Intelligence Report",
            status=ArtifactStatus.READY,
            content="## Approved report source\nGrounded content",
        )
    )
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=item.calendar_event_id,
            kind="linkedin_post",
            title="LinkedIn Growth Autopsy post",
            status=ArtifactStatus.SCHEDULED,
            source_id="depends:growth_autopsy",
        )
    )
    ai = DirectPostcallAI()

    result = await artifact_decision(
        report_id,
        DecisionRequest(decision="approve"),
        settings,
        store,
        ai,  # type: ignore[arg-type]
    )

    assert result["status"] == ArtifactStatus.APPROVED.value
    assert result["dependent_generation"]["status"] == "ready"
    post = store.get_artifact_by_kind(item.calendar_event_id, "linkedin_post")
    assert post is not None and post.status == ArtifactStatus.READY
    assert "linkedin_post" in ai.deliverables

    assert post.id is not None
    store.update_artifact(post.id, status=ArtifactStatus.FAILED)
    post = store.get_artifact(post.id)
    assert post is not None
    retried = await retry_postcall_artifact_direct(
        settings,
        store,
        ai,  # type: ignore[arg-type]
        post,
    )
    assert retried["status"] == "ready"
