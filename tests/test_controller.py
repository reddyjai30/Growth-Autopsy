from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from growth_autopsy.config import Settings
from growth_autopsy.controller import collect_agent_outputs, run_due_precall_research
from growth_autopsy.domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus
from growth_autopsy.store import WorkflowStore


class FakeResearcher:
    async def collect(self, appointment):
        return {
            "collected_at": "2026-08-06T10:00:00+00:00",
            "appointment": {"company": appointment.company},
            "website": {
                "final_url": appointment.website,
                "site_summary": {
                    "pages_successfully_analyzed": 1,
                    "pricing_page_observed": False,
                    "product_or_service_pages_observed": True,
                    "case_study_or_customer_page_observed": False,
                    "email_capture_observed": False,
                    "checkout_or_cart_link_observed": False,
                    "technologies_observed": [],
                },
                "pages": [{"url": appointment.website, "title": "Acme", "status_code": 200, "response_ms": 20, "h1": ["Acme"], "meta_description": "Acme", "cta_text": ["Buy"]}],
            },
            "pagespeed": {"mobile": {"status": "unavailable", "error": "test"}, "desktop": {"status": "unavailable", "error": "test"}},
            "public_search": {"queries": []},
            "unavailable_or_private_data": ["Private analytics required"],
        }


class FakeHermes:
    async def start_precall_run(self, appointment, evidence):
        assert evidence["website"]["final_url"] == appointment.website
        return "run-1"

    async def get_run(self, run_id):
        assert run_id == "run-1"
        return {"status": "completed", "output": "# Acme pre-call brief\n\nExactly sourced."}


@pytest.mark.asyncio
async def test_precall_pipeline_persists_evidence_and_report(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        lighthouse_executable=tmp_path / "lighthouse",
    ).resolve_paths(tmp_path)
    store = WorkflowStore(settings.database_path)
    store.initialize()
    now = datetime.now(UTC)
    appointment = Appointment(
        calendar_event_id="event-1",
        calendar_id="primary",
        etag="1",
        title="Acme",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="",
        founder_linkedin="",
        industry="Ecommerce",
        strategy_mode="auto",
        start_at=now + timedelta(minutes=30),
        end_at=now + timedelta(minutes=90),
        status=AppointmentStatus.RESEARCH_SCHEDULED,
        source_payload={},
        research_job_id="local:1",
        research_start_at=now - timedelta(minutes=1),
    )
    store.upsert_appointment(appointment)
    store.set_research_job("event-1", "local:1", appointment.research_start_at)
    store.upsert_artifact(Artifact(None, "event-1", "precall_research", "Research", ArtifactStatus.SCHEDULED))

    result = await run_due_precall_research(
        settings, store, FakeHermes(), now=now, researcher=FakeResearcher()  # type: ignore[arg-type]
    )
    assert result[0]["status"] == "synthesis_started"
    assert store.get_artifact_by_kind("event-1", "precall_evidence").status == ArtifactStatus.READY  # type: ignore[union-attr]

    collected = await collect_agent_outputs(settings, store, FakeHermes())  # type: ignore[arg-type]
    assert collected[0]["status"] == "ready"
    report = store.get_artifact_by_kind("event-1", "precall_research")
    assert report is not None and report.status == ArtifactStatus.READY
    assert report.file_path.endswith("precall-research.md")
    assert store.get_appointment("event-1").status == AppointmentStatus.RESEARCH_READY  # type: ignore[union-attr]
