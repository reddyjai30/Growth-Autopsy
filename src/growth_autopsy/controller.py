from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .ai import AIClient
from .calendar_ingestion import (
    CalendarIngestionService,
    GoogleCalendarGateway,
    calendar_conference_url,
)
from .config import Settings
from .domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus, RecordingStatus
from .hermes import HermesClient
from .linkedin import (
    LinkedInAmbiguousPublishError,
    LinkedInAuthorizationError,
    LinkedInClient,
    LinkedInPublishError,
    LinkedInTokenStore,
    extract_linkedin_commentary,
)
from .notion import NotionClient
from .postcall_framework import (
    FrameworkValidationError,
    extract_service_lane,
    validate_founder_intelligence,
    validate_postcall_deliverable,
)
from .research import FreePrecallResearcher, LocalPrecallScheduler, render_evidence_markdown
from .store import WorkflowStore


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _artifact_directory(settings: Settings, appointment: Appointment) -> Path:
    safe_event = "".join(char if char.isalnum() or char in "-_" else "_" for char in appointment.calendar_event_id)
    path = (settings.shared_workdir / "artifacts" / safe_event).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def build_calendar_service(
    settings: Settings,
    store: WorkflowStore,
) -> CalendarIngestionService:
    gateway = GoogleCalendarGateway(
        settings.google_token_file,
        settings.google_calendar_id,
        lookback_hours=settings.calendar_lookback_hours,
        lookahead_days=settings.calendar_lookahead_days,
    )
    if settings.precall_research_backend.casefold() != "local_free":
        raise RuntimeError(
            "Only GA_PRECALL_RESEARCH_BACKEND=local_free is supported by this build"
        )
    return CalendarIngestionService(
        gateway,
        store,
        LocalPrecallScheduler(),
        calendar_id=settings.google_calendar_id,
        title_prefix=settings.calendar_title_prefix,
        diksha_email=settings.diksha_email,
        precall_start_minutes=settings.precall_start_minutes,
        precall_delivery_minutes=settings.precall_delivery_minutes,
    )


async def sync_calendar_once(settings: Settings, store: WorkflowStore) -> dict[str, int]:
    result = await build_calendar_service(settings, store).sync()
    return {
        "scanned": result.scanned,
        "ignored": result.ignored,
        "scheduled": result.scheduled,
        "updated": result.updated,
        "cancelled": result.cancelled,
        "needs_input": result.needs_input,
    }


def record_sync_error(store: WorkflowStore, error: Exception) -> None:
    store.set_setting("calendar_last_error", str(error)[:1000])
    store.set_setting("calendar_last_sync_at", datetime.now(UTC).isoformat())


def _sync_message(store: WorkflowStore, result: dict[str, int]) -> None:
    store.set_setting("calendar_last_result", json.dumps(result, separators=(",", ":")))
    store.set_setting("calendar_last_error", "")
    store.set_setting("calendar_last_sync_at", datetime.now(UTC).isoformat())


def _persist_precall_evidence(
    settings: Settings,
    store: WorkflowStore,
    appointment: Appointment,
    evidence: dict[str, Any],
) -> tuple[Path, Path]:
    directory = _artifact_directory(settings, appointment)
    json_path = directory / "precall-evidence.json"
    markdown_path = directory / "precall-evidence.md"
    _atomic_write_text(json_path, json.dumps(evidence, ensure_ascii=False, indent=2))
    markdown = render_evidence_markdown(evidence)
    _atomic_write_text(markdown_path, markdown)
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=appointment.calendar_event_id,
            kind="precall_evidence",
            title=f"{appointment.company} evidence pack",
            status=ArtifactStatus.READY,
            source_id="local-free-collectors",
            file_path=str(markdown_path),
            content=markdown,
            notes=f"Raw JSON: {json_path}",
        )
    )
    return json_path, markdown_path


async def _run_precall_research(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    appointment: Appointment,
    artifact: Artifact,
    *,
    researcher: FreePrecallResearcher | None = None,
) -> dict[str, Any]:
    if artifact.id is None:
        raise RuntimeError("Pre-call artifact has no database id")
    claim_id = f"collect:{datetime.now(UTC).isoformat()}"
    if not store.claim_artifact_for_processing(
        artifact.id,
        allowed_statuses=(ArtifactStatus.SCHEDULED, ArtifactStatus.FAILED),
        source_id=claim_id,
        notes="Collecting deterministic public evidence",
    ):
        return {"event_id": appointment.calendar_event_id, "status": "already_claimed"}
    store.mark_appointment_status(appointment.calendar_event_id, AppointmentStatus.ANALYSIS_RUNNING)
    try:
        collector = researcher or FreePrecallResearcher(settings)
        evidence = await collector.collect(appointment)
        _persist_precall_evidence(settings, store, appointment, evidence)
        report = await ai.synthesize_precall(appointment, evidence)
        path = _artifact_directory(settings, appointment) / "precall-research.md"
        _atomic_write_text(path, report.rstrip() + "\n")
        store.update_artifact(
            artifact.id,
            status=ArtifactStatus.READY,
            source_id=f"ai:{settings.ai_model}",
            file_path=str(path),
            content=report,
            notes="Direct AI synthesis completed from the saved evidence pack",
        )
        store.mark_appointment_status(
            appointment.calendar_event_id, AppointmentStatus.RESEARCH_READY
        )
        return {
            "event_id": appointment.calendar_event_id,
            "status": "ready",
            "report_path": str(path),
        }
    except Exception as exc:
        message = str(exc)[:1000]
        store.update_artifact(
            artifact.id,
            status=ArtifactStatus.FAILED,
            notes=message,
        )
        store.mark_appointment_status(
            appointment.calendar_event_id, AppointmentStatus.FAILED, message
        )
        return {
            "event_id": appointment.calendar_event_id,
            "status": "failed",
            "error": message,
        }


async def run_due_precall_research(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    *,
    now: datetime | None = None,
    force_event_id: str | None = None,
    researcher: FreePrecallResearcher | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    candidates: list[tuple[Appointment, Artifact]] = []
    for appointment in store.list_appointments(limit=500):
        if force_event_id and appointment.calendar_event_id != force_event_id:
            continue
        if appointment.status == AppointmentStatus.CANCELLED or not appointment.website:
            continue
        artifact = store.get_artifact_by_kind(appointment.calendar_event_id, "precall_research")
        if artifact is None:
            if not force_event_id:
                continue
            artifact_id = store.upsert_artifact(
                Artifact(
                    id=None,
                    calendar_event_id=appointment.calendar_event_id,
                    kind="precall_research",
                    title=f"{appointment.company} pre-call intelligence",
                    status=ArtifactStatus.SCHEDULED,
                    source_id=appointment.research_job_id or "manual",
                    notes="Manually queued from dashboard",
                )
            )
            artifact = store.get_artifact(artifact_id)
            if artifact is None:
                continue
        stale_before = current - timedelta(
            minutes=settings.precall_collection_stale_minutes
        )
        if (
            artifact.status == ArtifactStatus.PROCESSING
            and artifact.source_id.startswith("collect:")
            and artifact.updated_at is not None
            and artifact.updated_at <= stale_before
        ):
            store.update_artifact(
                artifact.id or 0,
                status=ArtifactStatus.FAILED,
                notes="Recovered a stale evidence-collection claim after an interrupted worker",
            )
            artifact = store.get_artifact(artifact.id or 0) or artifact
        if force_event_id and artifact.status in {
            ArtifactStatus.READY,
            ArtifactStatus.APPROVED,
            ArtifactStatus.REVISION_REQUESTED,
        }:
            store.update_artifact(
                artifact.id or 0,
                status=ArtifactStatus.FAILED,
                notes="Manual re-run requested",
            )
            artifact = store.get_artifact(artifact.id or 0) or artifact
        due = force_event_id is not None or (
            appointment.research_start_at is not None
            and appointment.research_start_at <= current
        )
        if due and artifact.status in {ArtifactStatus.SCHEDULED, ArtifactStatus.FAILED}:
            candidates.append((appointment, artifact))

    semaphore = asyncio.Semaphore(settings.precall_max_parallel_appointments)

    async def run(item: tuple[Appointment, Artifact]) -> dict[str, Any]:
        async with semaphore:
            return await _run_precall_research(
                settings, store, ai, item[0], item[1], researcher=researcher
            )

    return await asyncio.gather(*(run(item) for item in candidates))


def _run_state(payload: dict[str, Any]) -> tuple[str, str, str]:
    run = payload.get("run") if isinstance(payload.get("run"), dict) else payload
    status = str(run.get("status") or payload.get("status") or "").casefold()
    output = ""
    for key in ("output", "final_response", "response", "result", "content"):
        value = run.get(key)
        if isinstance(value, str) and value.strip():
            output = value.strip()
            break
        if isinstance(value, dict):
            for nested in ("content", "text", "output", "response"):
                item = value.get(nested)
                if isinstance(item, str) and item.strip():
                    output = item.strip()
                    break
        if output:
            break
    error = str(run.get("error") or payload.get("error") or "")[:1000]
    return status, output, error


async def _collect_precall_run(
    settings: Settings,
    store: WorkflowStore,
    hermes: HermesClient,
    artifact: Artifact,
) -> dict[str, Any]:
    run_id = artifact.source_id.removeprefix("run:")
    payload = await hermes.get_run(run_id)
    run_status, output, error = _run_state(payload)
    if run_status in {"completed", "complete", "succeeded", "success"}:
        if not output:
            raise RuntimeError("Hermes marked the run complete without a report")
        appointment = store.get_appointment(artifact.calendar_event_id)
        if appointment is None:
            raise RuntimeError("Appointment disappeared while collecting the report")
        path = _artifact_directory(settings, appointment) / "precall-research.md"
        _atomic_write_text(path, output + "\n")
        store.update_artifact(
            artifact.id or 0,
            status=ArtifactStatus.READY,
            file_path=str(path),
            content=output,
            notes="Gemini synthesis completed from the saved evidence pack",
        )
        store.mark_appointment_status(
            artifact.calendar_event_id, AppointmentStatus.RESEARCH_READY
        )
        return {"artifact_id": artifact.id, "status": "ready"}
    if run_status in {"failed", "error", "cancelled", "canceled"}:
        message = error or f"Hermes pre-call run ended with status {run_status}"
        store.update_artifact(
            artifact.id or 0, status=ArtifactStatus.FAILED, notes=message
        )
        store.mark_appointment_status(
            artifact.calendar_event_id, AppointmentStatus.FAILED, message
        )
        return {"artifact_id": artifact.id, "status": "failed", "error": message}
    return {"artifact_id": artifact.id, "status": run_status or "running"}


async def _collect_postcall_run(
    settings: Settings,
    store: WorkflowStore,
    hermes: HermesClient,
    recording_id: int,
    run_id: str,
    event_id: str,
) -> dict[str, Any]:
    payload = await hermes.get_run(run_id)
    run_status, output, error = _run_state(payload)
    if run_status in {"completed", "complete", "succeeded", "success"} and output:
        appointment = store.get_appointment(event_id)
        if appointment is None:
            return {"recording_id": recording_id, "status": "unmatched"}
        try:
            validate_founder_intelligence(output)
        except FrameworkValidationError as exc:
            message = f"Hermes Founder Intelligence failed the production framework: {exc}"
            store.mark_recording_status(recording_id, RecordingStatus.FAILED, message[:1000])
            store.mark_appointment_status(event_id, AppointmentStatus.FAILED, message[:1000])
            return {"recording_id": recording_id, "status": "failed", "error": message}
        path = _artifact_directory(settings, appointment) / "founder-intelligence.md"
        _atomic_write_text(path, output + "\n")
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=event_id,
                kind="founder_intelligence",
                title=f"{appointment.company} founder intelligence",
                status=ArtifactStatus.READY,
                source_id=f"run:{run_id}",
                file_path=str(path),
                content=output,
                notes="Generated from the verified Fathom transcript",
            )
        )
        store.mark_recording_status(recording_id, RecordingStatus.ANALYSIS_COMPLETE)
        store.mark_appointment_status(event_id, AppointmentStatus.INTELLIGENCE_READY)
        queued = await queue_postcall_deliverables(
            store,
            hermes,
            appointment,
            founder_intelligence_path=str(path),
            founder_intelligence=output,
            linkedin_enabled=settings.linkedin_enabled,
        )
        return {"recording_id": recording_id, "status": "ready", "queued": queued}
    if run_status in {"completed", "complete", "succeeded", "success"}:
        message = "Hermes marked Founder Intelligence complete without an output"
        store.mark_recording_status(recording_id, RecordingStatus.FAILED, message)
        store.mark_appointment_status(event_id, AppointmentStatus.FAILED, message)
        return {"recording_id": recording_id, "status": "failed", "error": message}
    if run_status in {"failed", "error", "cancelled", "canceled"}:
        message = error or f"Hermes post-call run ended with status {run_status}"
        store.mark_recording_status(recording_id, RecordingStatus.FAILED, message)
        store.mark_appointment_status(event_id, AppointmentStatus.FAILED, message)
        return {"recording_id": recording_id, "status": "failed", "error": message}
    return {"recording_id": recording_id, "status": run_status or "running"}


DELIVERABLES: dict[str, tuple[str, str]] = {
    "growth_autopsy": ("growth-intelligence-report.md", "Growth Intelligence Report"),
    "linkedin_post": ("linkedin-growth-autopsy-post.md", "LinkedIn Growth Autopsy post"),
    "strategy_doc": ("one-problem-strategy.md", "one-problem Strategy Doc"),
    "pitch_deck_brief": ("pitch-deck-brief.md", "Gamma-ready pitch deck"),
}

DEPENDENT_DELIVERABLES = {
    "growth_autopsy": "linkedin_post",
    "strategy_doc": "pitch_deck_brief",
}
PARENT_DELIVERABLES = {
    child: parent for parent, child in DEPENDENT_DELIVERABLES.items()
}
LINKEDIN_ARTIFACT_KINDS = frozenset({"linkedin_post", "linkedin_publication"})


def _dependent_deliverable_kind(
    parent_kind: str,
    *,
    linkedin_enabled: bool,
) -> str | None:
    child_kind = DEPENDENT_DELIVERABLES.get(parent_kind)
    if child_kind == "linkedin_post" and not linkedin_enabled:
        return None
    return child_kind


def classify_strategy_intent(appointment: Appointment, founder_intelligence: str) -> str:
    configured = appointment.strategy_mode.strip().casefold().replace("-", "_")
    if configured in {"case_study_and_strategy", "strategy", "strategy_requested", "yes"}:
        return "strategy_requested"
    if configured in {"case_study_only", "no_strategy", "no"}:
        return "case_study_only"
    matches = re.findall(
        r"strategy_intent\s*:\s*(strategy_requested|case_study_only|unsure)",
        founder_intelligence,
        flags=re.I,
    )
    return matches[-1].casefold() if matches else "unsure"


def classify_service_lane(founder_intelligence: str) -> str:
    return extract_service_lane(founder_intelligence)


def _schedule_dependent_deliverable(
    store: WorkflowStore,
    appointment: Appointment,
    parent_kind: str,
    *,
    linkedin_enabled: bool,
) -> None:
    child_kind = _dependent_deliverable_kind(
        parent_kind,
        linkedin_enabled=linkedin_enabled,
    )
    if child_kind is None or store.get_artifact_by_kind(
        appointment.calendar_event_id, child_kind
    ):
        return
    _, title = DELIVERABLES[child_kind]
    parent_label = DELIVERABLES[parent_kind][1]
    store.upsert_artifact(
        Artifact(
            id=None,
            calendar_event_id=appointment.calendar_event_id,
            kind=child_kind,
            title=f"{appointment.company} {title}",
            status=ArtifactStatus.SCHEDULED,
            source_id=f"depends:{parent_kind}",
            notes=f"Waiting for Diksha to approve the {parent_label}",
        )
    )


def invalidate_dependent_deliverable(
    settings: Settings,
    store: WorkflowStore,
    parent: Artifact,
) -> None:
    child_kind = _dependent_deliverable_kind(
        parent.kind,
        linkedin_enabled=settings.linkedin_enabled,
    )
    if child_kind is None:
        return
    child = store.get_artifact_by_kind(parent.calendar_event_id, child_kind)
    if child is None or child.id is None:
        return
    store.update_artifact(
        child.id,
        status=ArtifactStatus.SCHEDULED,
        source_id=f"depends:{parent.kind}",
        file_path="",
        content="",
        notes=f"Parent {DELIVERABLES[parent.kind][1]} changed; regenerate after approval",
    )


def _precall_report_content(store: WorkflowStore, event_id: str) -> str:
    artifact = store.get_artifact_by_kind(event_id, "precall_research")
    if artifact is None:
        return ""
    if artifact.content.strip():
        return artifact.content.strip()
    if artifact.file_path:
        path = Path(artifact.file_path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


async def _generate_direct_deliverable(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    appointment: Appointment,
    kind: str,
    founder_intelligence: str,
    precall_report: str,
    *,
    source_document: str = "",
) -> dict[str, Any]:
    existing = store.get_artifact_by_kind(appointment.calendar_event_id, kind)
    if existing and existing.status == ArtifactStatus.APPROVED and existing.content.strip():
        return {"kind": kind, "status": "already_approved", "artifact_id": existing.id}
    if existing and existing.status == ArtifactStatus.READY and existing.content.strip():
        try:
            validate_postcall_deliverable(
                kind,
                existing.content,
                brand=appointment.company,
                has_external_research=bool(precall_report.strip()),
                service_lane=classify_service_lane(founder_intelligence),
            )
        except FrameworkValidationError as exc:
            if existing.id is not None:
                store.update_artifact(
                    existing.id,
                    status=ArtifactStatus.FAILED,
                    notes=f"Legacy draft requires v2 regeneration: {exc}"[:1000],
                )
            existing.status = ArtifactStatus.FAILED
        else:
            return {"kind": kind, "status": "already_ready", "artifact_id": existing.id}
    filename, title = DELIVERABLES[kind]
    if existing and existing.status == ArtifactStatus.PROCESSING:
        return {"kind": kind, "status": "already_processing", "artifact_id": existing.id}
    if existing and existing.id is not None:
        artifact_id = existing.id
        claimed = store.claim_artifact_for_processing(
            artifact_id,
            allowed_statuses=(
                ArtifactStatus.SCHEDULED,
                ArtifactStatus.FAILED,
                ArtifactStatus.REVISION_REQUESTED,
            ),
            source_id=f"direct:{ai.model}",
            notes="Direct AI draft generation is running",
        )
        if not claimed:
            return {
                "kind": kind,
                "status": existing.status.value.casefold(),
                "artifact_id": artifact_id,
            }
    else:
        artifact_id = store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind=kind,
                title=f"{appointment.company} {title}",
                status=ArtifactStatus.PROCESSING,
                source_id=f"direct:{ai.model}",
                notes="Direct AI draft generation is running",
            )
        )
    try:
        document = await ai.synthesize_postcall_deliverable(
            kind,
            appointment,
            founder_intelligence,
            precall_report,
            source_document=source_document,
            service_lane=classify_service_lane(founder_intelligence),
        )
        path = _artifact_directory(settings, appointment) / filename
        _atomic_write_text(path, document.rstrip() + "\n")
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.READY,
            file_path=str(path),
            content=document,
            notes=(
                "Generated from the approved parent document; waiting for Diksha approval"
                if source_document
                else "Direct AI draft completed; waiting for Diksha approval"
            ),
        )
        return {"kind": kind, "status": "ready", "artifact_id": artifact_id}
    except Exception as exc:
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.FAILED,
            notes=str(exc)[:1000],
        )
        raise


async def run_direct_postcall_analysis(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    recording_id: int,
) -> dict[str, Any]:
    recording = store.get_recording(recording_id)
    if recording is None:
        return {"recording_id": recording_id, "status": "missing"}
    if recording.status == RecordingStatus.ANALYSIS_COMPLETE:
        return {"recording_id": recording_id, "status": "already_complete"}
    if not recording.calendar_event_id:
        return {"recording_id": recording_id, "status": "unmatched"}
    appointment = store.get_appointment(recording.calendar_event_id)
    if appointment is None:
        return {"recording_id": recording_id, "status": "unmatched"}
    precall_report = _precall_report_content(store, appointment.calendar_event_id)
    try:
        intelligence = store.get_artifact_by_kind(
            appointment.calendar_event_id, "founder_intelligence"
        )
        if (
            intelligence is not None
            and intelligence.status in {ArtifactStatus.READY, ArtifactStatus.APPROVED}
            and intelligence.content.strip()
        ):
            try:
                validate_founder_intelligence(intelligence.content)
            except FrameworkValidationError:
                founder_intelligence = await ai.synthesize_founder_intelligence(
                    appointment,
                    recording.payload,
                    precall_report,
                )
            else:
                founder_intelligence = intelligence.content
        else:
            founder_intelligence = await ai.synthesize_founder_intelligence(
                appointment,
                recording.payload,
                precall_report,
            )
        if intelligence is None or founder_intelligence != intelligence.content:
            path = _artifact_directory(settings, appointment) / "founder-intelligence.md"
            _atomic_write_text(path, founder_intelligence.rstrip() + "\n")
            store.upsert_artifact(
                Artifact(
                    id=None,
                    calendar_event_id=appointment.calendar_event_id,
                    kind="founder_intelligence",
                    title=f"{appointment.company} founder intelligence",
                    status=ArtifactStatus.READY,
                    source_id=f"direct:{ai.model}",
                    file_path=str(path),
                    content=founder_intelligence,
                    notes="Generated directly from the verified Fathom payload",
                )
            )
        store.mark_appointment_status(
            appointment.calendar_event_id, AppointmentStatus.INTELLIGENCE_READY
        )
        intent = classify_strategy_intent(appointment, founder_intelligence)
        store.set_setting(f"strategy_intent:{appointment.calendar_event_id}", intent)
        kinds = ["growth_autopsy"]
        if intent == "strategy_requested":
            kinds.append("strategy_doc")
        elif intent == "unsure":
            store.upsert_artifact(
                Artifact(
                    id=None,
                    calendar_event_id=appointment.calendar_event_id,
                    kind="strategy_decision",
                    title=f"{appointment.company} strategy decision",
                    status=ArtifactStatus.READY,
                    content=(
                        "Strategy intent was ambiguous. Diksha must choose "
                        "strategy_requested or case_study_only before strategy/deck generation."
                    ),
                    notes="Human routing decision required",
                )
            )
        generated: list[dict[str, Any]] = []
        for kind in kinds:
            generated.append(
                await _generate_direct_deliverable(
                    settings,
                    store,
                    ai,
                    appointment,
                    kind,
                    founder_intelligence,
                    precall_report,
                )
            )
            _schedule_dependent_deliverable(
                store,
                appointment,
                kind,
                linkedin_enabled=settings.linkedin_enabled,
            )
        store.mark_recording_status(recording_id, RecordingStatus.ANALYSIS_COMPLETE)
        _refresh_content_status(store, appointment.calendar_event_id)
        return {
            "recording_id": recording_id,
            "status": "ready",
            "strategy_intent": intent,
            "generated": generated,
        }
    except Exception as exc:
        message = str(exc)[:1000]
        store.mark_recording_status(recording_id, RecordingStatus.FAILED, message)
        store.mark_appointment_status(
            appointment.calendar_event_id, AppointmentStatus.FAILED, message
        )
        raise


async def run_pending_postcall_analysis(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for recording in store.list_recordings():
        if (
            recording.status == RecordingStatus.FAILED
            and not recording.analysis_run_id
            and "HERMES" in (recording.last_error or "").upper()
            and recording.calendar_event_id
        ):
            store.set_recording_analysis_run(
                recording.recording_id, f"direct:{recording.recording_id}"
            )
            store.mark_appointment_status(
                recording.calendar_event_id, AppointmentStatus.ANALYSIS_RUNNING
            )
            recording = store.get_recording(recording.recording_id) or recording
        if (
            recording.status != RecordingStatus.ANALYSIS_RUNNING
            or not recording.analysis_run_id
            or not recording.analysis_run_id.startswith("direct:")
        ):
            continue
        try:
            results.append(
                await run_direct_postcall_analysis(
                    settings, store, ai, recording.recording_id
                )
            )
        except Exception as exc:
            results.append(
                {
                    "recording_id": recording.recording_id,
                    "status": "failed",
                    "error": str(exc)[:1000],
                }
            )
    return results


async def resolve_strategy_decision_direct(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    event_id: str,
    intent: str,
) -> list[dict[str, Any]]:
    if intent not in {"strategy_requested", "case_study_only"}:
        raise ValueError("Strategy decision must be strategy_requested or case_study_only")
    appointment = store.get_appointment(event_id)
    intelligence = store.get_artifact_by_kind(event_id, "founder_intelligence")
    if appointment is None or intelligence is None or not intelligence.content.strip():
        raise ValueError("Founder Intelligence is required before routing deliverables")
    decision = store.get_artifact_by_kind(event_id, "strategy_decision")
    if decision and decision.id:
        store.update_artifact(
            decision.id,
            status=ArtifactStatus.APPROVED,
            notes=f"Diksha selected {intent}",
        )
    store.set_setting(f"strategy_intent:{event_id}", intent)
    kinds = ["growth_autopsy"]
    if intent == "strategy_requested":
        kinds.append("strategy_doc")
    precall_report = _precall_report_content(store, event_id)
    results: list[dict[str, Any]] = []
    for kind in kinds:
        results.append(
            await _generate_direct_deliverable(
                settings,
                store,
                ai,
                appointment,
                kind,
                intelligence.content,
                precall_report,
            )
        )
        _schedule_dependent_deliverable(
            store,
            appointment,
            kind,
            linkedin_enabled=settings.linkedin_enabled,
        )
    _refresh_content_status(store, event_id)
    return results


async def generate_dependent_deliverable_direct(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    parent: Artifact,
) -> dict[str, Any] | None:
    """Generate a child only after its authoritative parent is approved."""

    child_kind = _dependent_deliverable_kind(
        parent.kind,
        linkedin_enabled=settings.linkedin_enabled,
    )
    if child_kind is None:
        return None
    if parent.status != ArtifactStatus.APPROVED or not parent.content.strip():
        raise ValueError("The parent document must be approved before generating its child")
    appointment = store.get_appointment(parent.calendar_event_id)
    intelligence = store.get_artifact_by_kind(
        parent.calendar_event_id, "founder_intelligence"
    )
    if appointment is None or intelligence is None or not intelligence.content.strip():
        raise ValueError("Founder Intelligence is required for dependent generation")
    return await _generate_direct_deliverable(
        settings,
        store,
        ai,
        appointment,
        child_kind,
        intelligence.content,
        _precall_report_content(store, parent.calendar_event_id),
        source_document=parent.content,
    )


async def retry_postcall_artifact_direct(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    artifact: Artifact,
) -> dict[str, Any]:
    if artifact.kind == "linkedin_post" and not settings.linkedin_enabled:
        raise ValueError("LinkedIn workflow is temporarily disabled")
    if artifact.kind not in DELIVERABLES:
        raise ValueError("Only post-call documents can be retried here")
    if artifact.status != ArtifactStatus.FAILED:
        raise ValueError("Only a failed post-call document can be retried")
    appointment = store.get_appointment(artifact.calendar_event_id)
    intelligence = store.get_artifact_by_kind(
        artifact.calendar_event_id, "founder_intelligence"
    )
    if appointment is None or intelligence is None or not intelligence.content.strip():
        raise ValueError("Founder Intelligence is required before retrying this document")
    source_document = ""
    parent_kind = PARENT_DELIVERABLES.get(artifact.kind)
    if parent_kind:
        parent = store.get_artifact_by_kind(artifact.calendar_event_id, parent_kind)
        if (
            parent is None
            or parent.status != ArtifactStatus.APPROVED
            or not parent.content.strip()
        ):
            raise ValueError("Approve the parent document before retrying its dependent")
        source_document = parent.content
    return await _generate_direct_deliverable(
        settings,
        store,
        ai,
        appointment,
        artifact.kind,
        intelligence.content,
        _precall_report_content(store, artifact.calendar_event_id),
        source_document=source_document,
    )


async def revise_postcall_artifact_direct(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    artifact: Artifact,
    revision_notes: str,
) -> dict[str, Any]:
    if artifact.kind == "linkedin_post" and not settings.linkedin_enabled:
        raise ValueError("LinkedIn workflow is temporarily disabled")
    appointment = store.get_appointment(artifact.calendar_event_id)
    if appointment is None:
        raise ValueError("Appointment not found")
    if artifact.kind not in DELIVERABLES:
        raise ValueError("Only generated post-call drafts can be revised")
    if not artifact.content.strip():
        raise ValueError("Current draft content is unavailable")
    store.update_artifact(
        artifact.id or 0,
        status=ArtifactStatus.PROCESSING,
        notes=f"Direct AI revision running: {revision_notes}",
    )
    intelligence = store.get_artifact_by_kind(
        artifact.calendar_event_id, "founder_intelligence"
    )
    parent_kind = PARENT_DELIVERABLES.get(artifact.kind)
    parent = (
        store.get_artifact_by_kind(artifact.calendar_event_id, parent_kind)
        if parent_kind
        else None
    )
    try:
        revised = await ai.revise_postcall_deliverable(
            artifact.kind,
            artifact.content,
            revision_notes,
            brand=appointment.company,
            has_external_research=bool(
                _precall_report_content(store, artifact.calendar_event_id).strip()
            ),
            service_lane=classify_service_lane(
                intelligence.content if intelligence else ""
            ),
            source_document=parent.content if parent else "",
        )
        filename, _ = DELIVERABLES[artifact.kind]
        path = Path(artifact.file_path) if artifact.file_path else (
            _artifact_directory(settings, appointment) / filename
        )
        _atomic_write_text(path, revised.rstrip() + "\n")
        store.update_artifact(
            artifact.id or 0,
            status=ArtifactStatus.READY,
            source_id=f"direct:{ai.model}",
            file_path=str(path),
            content=revised,
            notes="Direct AI revision completed; waiting for Diksha approval",
        )
        invalidate_dependent_deliverable(settings, store, artifact)
        return {"artifact_id": artifact.id, "status": ArtifactStatus.READY.value}
    except Exception as exc:
        store.update_artifact(
            artifact.id or 0,
            status=ArtifactStatus.FAILED,
            notes=str(exc)[:1000],
        )
        raise


async def _queue_deliverable(
    store: WorkflowStore,
    hermes: HermesClient,
    appointment: Appointment,
    kind: str,
    founder_intelligence_path: str,
    precall_report_path: str,
) -> dict[str, Any]:
    existing = store.get_artifact_by_kind(appointment.calendar_event_id, kind)
    if existing and existing.status not in {ArtifactStatus.FAILED, ArtifactStatus.REVISION_REQUESTED}:
        return {"kind": kind, "status": "already_queued"}
    try:
        run_id = await hermes.start_deliverable_run(
            kind,
            appointment,
            founder_intelligence_path=founder_intelligence_path,
            precall_report_path=precall_report_path,
        )
        _, title = DELIVERABLES[kind]
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind=kind,
                title=f"{appointment.company} {title}",
                status=ArtifactStatus.PROCESSING,
                source_id=f"run:{run_id}",
                notes="Hermes draft generation is running",
            )
        )
        return {"kind": kind, "status": "started", "run_id": run_id}
    except Exception as exc:
        _, title = DELIVERABLES[kind]
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind=kind,
                title=f"{appointment.company} {title}",
                status=ArtifactStatus.FAILED,
                notes=str(exc)[:1000],
            )
        )
        return {"kind": kind, "status": "failed", "error": str(exc)[:1000]}


async def queue_postcall_deliverables(
    store: WorkflowStore,
    hermes: HermesClient,
    appointment: Appointment,
    *,
    founder_intelligence_path: str,
    founder_intelligence: str,
    intent_override: str | None = None,
    linkedin_enabled: bool = False,
) -> list[dict[str, Any]]:
    intent = intent_override or classify_strategy_intent(appointment, founder_intelligence)
    if intent not in {"strategy_requested", "case_study_only", "unsure"}:
        raise ValueError("Invalid strategy intent")
    store.set_setting(f"strategy_intent:{appointment.calendar_event_id}", intent)
    precall = store.get_artifact_by_kind(appointment.calendar_event_id, "precall_research")
    precall_path = precall.file_path if precall else ""
    kinds = ["growth_autopsy"]
    if intent == "strategy_requested":
        kinds.append("strategy_doc")
    elif intent == "unsure":
        store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=appointment.calendar_event_id,
                kind="strategy_decision",
                title=f"{appointment.company} strategy decision",
                status=ArtifactStatus.READY,
                content=(
                    "Strategy intent was ambiguous. Diksha must choose "
                    "strategy_requested or case_study_only before strategy/deck generation."
                ),
                notes="Human routing decision required",
            )
        )
    results = await asyncio.gather(
        *(
            _queue_deliverable(
                store,
                hermes,
                appointment,
                kind,
                founder_intelligence_path,
                precall_path,
            )
            for kind in kinds
        )
    )
    for kind in kinds:
        _schedule_dependent_deliverable(
            store,
            appointment,
            kind,
            linkedin_enabled=linkedin_enabled,
        )
    _refresh_content_status(store, appointment.calendar_event_id)
    return results


def _refresh_content_status(store: WorkflowStore, event_id: str) -> None:
    intent = store.get_setting(f"strategy_intent:{event_id}") or "unsure"
    if intent == "unsure":
        return
    required = ["growth_autopsy"]
    if intent == "strategy_requested":
        required.append("strategy_doc")
    artifacts = [store.get_artifact_by_kind(event_id, kind) for kind in required]
    ready = {ArtifactStatus.READY, ArtifactStatus.APPROVED, ArtifactStatus.REVISION_REQUESTED}
    if all(item is not None and item.status in ready for item in artifacts):
        store.mark_appointment_status(event_id, AppointmentStatus.CONTENT_DRAFTED)


async def _collect_deliverable_run(
    settings: Settings,
    store: WorkflowStore,
    hermes: HermesClient,
    artifact: Artifact,
) -> dict[str, Any]:
    run_id = artifact.source_id.removeprefix("run:")
    payload = await hermes.get_run(run_id)
    run_status, output, error = _run_state(payload)
    completed = {"completed", "complete", "succeeded", "success"}
    if run_status in completed:
        if not output:
            message = "Hermes marked the deliverable complete without an output"
            store.update_artifact(artifact.id or 0, status=ArtifactStatus.FAILED, notes=message)
            return {"artifact_id": artifact.id, "status": "failed", "error": message}
        appointment = store.get_appointment(artifact.calendar_event_id)
        if appointment is None:
            raise RuntimeError("Appointment disappeared while collecting a deliverable")
        intelligence = store.get_artifact_by_kind(
            artifact.calendar_event_id, "founder_intelligence"
        )
        try:
            validate_postcall_deliverable(
                artifact.kind,
                output,
                brand=appointment.company,
                has_external_research=bool(
                    _precall_report_content(store, artifact.calendar_event_id).strip()
                ),
                service_lane=classify_service_lane(
                    intelligence.content if intelligence else ""
                ),
            )
        except FrameworkValidationError as exc:
            message = f"Hermes output failed the production framework: {exc}"
            store.update_artifact(
                artifact.id or 0,
                status=ArtifactStatus.FAILED,
                notes=message[:1000],
            )
            return {"artifact_id": artifact.id, "status": "failed", "error": message}
        filename, _ = DELIVERABLES[artifact.kind]
        path = _artifact_directory(settings, appointment) / filename
        _atomic_write_text(path, output.rstrip() + "\n")
        store.update_artifact(
            artifact.id or 0,
            status=ArtifactStatus.READY,
            file_path=str(path),
            content=output,
            notes="Hermes draft completed; waiting for Diksha approval",
        )
        _schedule_dependent_deliverable(
            store,
            appointment,
            artifact.kind,
            linkedin_enabled=settings.linkedin_enabled,
        )
        _refresh_content_status(store, artifact.calendar_event_id)
        return {"artifact_id": artifact.id, "status": "ready", "kind": artifact.kind}
    if run_status in {"failed", "error", "cancelled", "canceled"}:
        message = error or f"Hermes {artifact.kind} run ended with status {run_status}"
        store.update_artifact(artifact.id or 0, status=ArtifactStatus.FAILED, notes=message)
        store.mark_appointment_status(artifact.calendar_event_id, AppointmentStatus.FAILED, message)
        return {"artifact_id": artifact.id, "status": "failed", "error": message}
    return {"artifact_id": artifact.id, "status": run_status or "running"}


async def collect_agent_outputs(
    settings: Settings, store: WorkflowStore, hermes: HermesClient
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for artifact in store.list_artifacts(statuses=(ArtifactStatus.PROCESSING,)):
        if not artifact.source_id.startswith("run:"):
            continue
        try:
            if artifact.kind == "precall_research":
                results.append(await _collect_precall_run(settings, store, hermes, artifact))
            elif artifact.kind in DELIVERABLES:
                results.append(
                    await _collect_deliverable_run(settings, store, hermes, artifact)
                )
        except Exception as exc:
            results.append(
                {"artifact_id": artifact.id, "status": "poll_error", "error": str(exc)[:1000]}
            )
    for recording in store.list_recordings():
        if (
            recording.status != RecordingStatus.ANALYSIS_RUNNING
            or not recording.analysis_run_id
            or not recording.calendar_event_id
        ):
            continue
        try:
            results.append(
                await _collect_postcall_run(
                    settings,
                    store,
                    hermes,
                    recording.recording_id,
                    recording.analysis_run_id,
                    recording.calendar_event_id,
                )
            )
        except Exception as exc:
            results.append(
                {"recording_id": recording.recording_id, "status": "poll_error", "error": str(exc)[:1000]}
            )
    return results


async def resolve_strategy_decision(
    store: WorkflowStore,
    hermes: HermesClient,
    event_id: str,
    intent: str,
    *,
    linkedin_enabled: bool = False,
) -> list[dict[str, Any]]:
    if intent not in {"strategy_requested", "case_study_only"}:
        raise ValueError("Strategy decision must be strategy_requested or case_study_only")
    appointment = store.get_appointment(event_id)
    intelligence = store.get_artifact_by_kind(event_id, "founder_intelligence")
    if appointment is None or intelligence is None or not intelligence.file_path:
        raise ValueError("Founder Intelligence is required before routing deliverables")
    decision = store.get_artifact_by_kind(event_id, "strategy_decision")
    if decision and decision.id:
        store.update_artifact(
            decision.id,
            status=ArtifactStatus.APPROVED,
            notes=f"Diksha selected {intent}",
        )
    return await queue_postcall_deliverables(
        store,
        hermes,
        appointment,
        founder_intelligence_path=intelligence.file_path,
        founder_intelligence=intelligence.content,
        intent_override=intent,
        linkedin_enabled=linkedin_enabled,
    )


async def publish_approved_package(
    settings: Settings,
    store: WorkflowStore,
    event_id: str,
    *,
    notion: NotionClient | None = None,
    mark_published: bool = True,
) -> dict[str, Any]:
    appointment = store.get_appointment(event_id)
    if appointment is None:
        raise ValueError("Appointment not found")
    client = notion or NotionClient(
        settings.notion_api_key,
        settings.notion_parent_page_id,
        api_version=settings.notion_api_version,
    )
    if not client.configured:
        return {"status": "not_configured"}
    existing = store.get_artifact_by_kind(event_id, "notion_package")
    if existing and existing.source_id and not existing.source_id.startswith("publish:"):
        return {
            "status": "already_published",
            "page_id": existing.source_id,
            "url": existing.content,
        }
    intent = store.get_setting(f"strategy_intent:{event_id}") or "unsure"
    if intent == "unsure":
        return {"status": "waiting_for_strategy_decision"}
    required = ["growth_autopsy"]
    if settings.linkedin_enabled:
        required.append("linkedin_post")
    if intent == "strategy_requested":
        required.extend(["strategy_doc", "pitch_deck_brief"])
    artifacts = [store.get_artifact_by_kind(event_id, kind) for kind in required]
    waiting = [
        kind
        for kind, item in zip(required, artifacts, strict=True)
        if item is None or item.status != ArtifactStatus.APPROVED
    ]
    if waiting:
        return {"status": "waiting_for_approval", "artifacts": waiting}
    if existing is None:
        artifact_id = store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=event_id,
                kind="notion_package",
                title=f"{appointment.company} Notion package",
                status=ArtifactStatus.SCHEDULED,
                notes="Approval complete; Notion publication queued",
            )
        )
    else:
        artifact_id = existing.id or 0
    claim = f"publish:{datetime.now(UTC).isoformat()}"
    if not store.claim_artifact_for_processing(
        artifact_id,
        allowed_statuses=(ArtifactStatus.SCHEDULED, ArtifactStatus.FAILED),
        source_id=claim,
        notes="Creating the approved private Notion page",
    ):
        return {"status": "publish_in_progress"}
    sections = [f"# {appointment.company} — Growth Autopsy package"]
    for item in artifacts:
        assert item is not None
        sections.extend(["", "---", "", f"## {item.title}", "", item.content.strip()])
    markdown = "\n".join(sections).rstrip() + "\n"
    try:
        payload = await client.create_markdown_page(markdown)
    except Exception as exc:
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.FAILED,
            notes=str(exc)[:1000],
        )
        raise
    page_id = str(payload["id"])
    page_url = str(payload.get("url") or "")
    store.update_artifact(
        artifact_id,
        status=ArtifactStatus.READY,
        source_id=page_id,
        content=page_url,
        notes="Approved package created as a private Notion child page",
    )
    if mark_published:
        store.mark_appointment_status(event_id, AppointmentStatus.PUBLISHED)
    return {"status": "published", "page_id": page_id, "url": page_url}


async def publish_approved_linkedin_post(
    settings: Settings,
    store: WorkflowStore,
    event_id: str,
    *,
    linkedin: LinkedInClient | None = None,
    token_store: LinkedInTokenStore | None = None,
) -> dict[str, Any]:
    if not settings.linkedin_enabled:
        return {"status": "disabled"}
    appointment = store.get_appointment(event_id)
    if appointment is None:
        raise ValueError("Appointment not found")
    existing = store.get_artifact_by_kind(event_id, "linkedin_publication")
    if existing and existing.source_id.startswith("urn:li:"):
        return {
            "status": "already_published",
            "post_id": existing.source_id,
            "url": existing.content,
        }
    if existing and existing.status == ArtifactStatus.REVISION_REQUESTED:
        return {
            "status": "verification_required",
            "error": existing.notes,
        }
    approved = store.get_artifact_by_kind(event_id, "linkedin_post")
    if approved is None or approved.status != ArtifactStatus.APPROVED:
        return {"status": "waiting_for_approval", "artifacts": ["linkedin_post"]}
    client = linkedin or LinkedInClient(
        settings.linkedin_client_id,
        settings.linkedin_client_secret,
        settings.linkedin_redirect_uri,
        api_version=settings.linkedin_api_version,
    )
    if not client.configured:
        return {"status": "not_configured"}
    commentary = extract_linkedin_commentary(approved.content)
    if existing is None:
        artifact_id = store.upsert_artifact(
            Artifact(
                id=None,
                calendar_event_id=event_id,
                kind="linkedin_publication",
                title=f"{appointment.company} LinkedIn publication",
                status=ArtifactStatus.SCHEDULED,
                notes="Notion package published; LinkedIn publication queued",
            )
        )
    else:
        artifact_id = existing.id or 0
    claim = f"publish:{datetime.now(UTC).isoformat()}"
    if not store.claim_artifact_for_processing(
        artifact_id,
        allowed_statuses=(ArtifactStatus.SCHEDULED, ArtifactStatus.FAILED),
        source_id=claim,
        notes="Publishing the approved post to the connected LinkedIn profile",
    ):
        return {"status": "publish_in_progress"}
    credentials = token_store or LinkedInTokenStore(settings.linkedin_token_file)
    try:
        token = credentials.load()
        payload = await client.publish_text(token, commentary)
    except LinkedInAuthorizationError as exc:
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.FAILED,
            notes=str(exc)[:1000],
        )
        return {"status": "authorization_required", "error": str(exc)}
    except LinkedInAmbiguousPublishError as exc:
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.REVISION_REQUESTED,
            notes=str(exc)[:1000],
        )
        return {"status": "verification_required", "error": str(exc)}
    except LinkedInPublishError as exc:
        store.update_artifact(
            artifact_id,
            status=ArtifactStatus.FAILED,
            notes=str(exc)[:1000],
        )
        raise
    store.update_artifact(
        artifact_id,
        status=ArtifactStatus.READY,
        source_id=payload["post_id"],
        content=payload["url"],
        notes="Approved Growth Autopsy post published to LinkedIn",
    )
    return {"status": "published", **payload}


async def publish_approved_distribution(
    settings: Settings,
    store: WorkflowStore,
    event_id: str,
    *,
    notion: NotionClient | None = None,
    linkedin: LinkedInClient | None = None,
    token_store: LinkedInTokenStore | None = None,
) -> dict[str, Any]:
    """Publish Notion first, then LinkedIn, without duplicating either write."""

    linkedin_should_publish = (
        settings.linkedin_enabled and settings.linkedin_publish_after_notion
    )
    notion_result = await publish_approved_package(
        settings,
        store,
        event_id,
        notion=notion,
        mark_published=not linkedin_should_publish,
    )
    if notion_result["status"] not in {"published", "already_published"}:
        return {"status": notion_result["status"], "notion": notion_result}
    if not linkedin_should_publish:
        return notion_result
    linkedin_result = await publish_approved_linkedin_post(
        settings,
        store,
        event_id,
        linkedin=linkedin,
        token_store=token_store,
    )
    if linkedin_result["status"] in {"published", "already_published"}:
        store.mark_appointment_status(event_id, AppointmentStatus.PUBLISHED)
        return {
            "status": "published",
            "notion": notion_result,
            "linkedin": linkedin_result,
        }
    return {
        "status": f"linkedin_{linkedin_result['status']}",
        "notion": notion_result,
        "linkedin": linkedin_result,
    }


async def resolve_linkedin_publication_uncertainty(
    settings: Settings,
    store: WorkflowStore,
    event_id: str,
    outcome: str,
    *,
    post_url: str = "",
) -> dict[str, Any]:
    if not settings.linkedin_enabled:
        return {"status": "disabled"}
    publication = store.get_artifact_by_kind(event_id, "linkedin_publication")
    if publication is None or publication.status != ArtifactStatus.REVISION_REQUESTED:
        raise ValueError("There is no uncertain LinkedIn publication to resolve")
    normalized = outcome.strip().casefold()
    if normalized == "retry":
        store.update_artifact(
            publication.id or 0,
            status=ArtifactStatus.FAILED,
            source_id="",
            notes="Operator checked the connected profile, found no post, and approved a retry",
        )
        return await publish_approved_distribution(settings, store, event_id)
    if normalized != "published":
        raise ValueError("Outcome must be published or retry")
    candidate = post_url.strip()
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ):
        raise ValueError("Paste the HTTPS URL of the post on linkedin.com")
    match = re.search(
        r"(?:urn:li:(?:share|ugcPost|activity):|activity-)(\d+)",
        candidate,
        flags=re.I,
    )
    if not match:
        raise ValueError("The LinkedIn post URL does not contain a recognizable activity ID")
    post_id = f"urn:li:activity:{match.group(1)}"
    store.update_artifact(
        publication.id or 0,
        status=ArtifactStatus.READY,
        source_id=post_id,
        content=candidate,
        notes="Operator verified that the uncertain request created this LinkedIn post",
    )
    store.mark_appointment_status(event_id, AppointmentStatus.PUBLISHED)
    return {"status": "recorded", "post_id": post_id, "url": candidate}


async def automation_loop(
    settings: Settings,
    store: WorkflowStore,
    ai: AIClient,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            sync_result = await sync_calendar_once(settings, store)
            _sync_message(store, sync_result)
        except Exception as exc:
            record_sync_error(store, exc)
        try:
            await run_due_precall_research(settings, store, ai)
        except Exception as exc:
            store.set_setting("automation_last_error", str(exc)[:1000])
        try:
            await run_pending_postcall_analysis(settings, store, ai)
        except Exception as exc:
            store.set_setting("automation_last_error", str(exc)[:1000])
        store.set_setting("automation_last_tick_at", datetime.now(UTC).isoformat())
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.background_sync_interval_seconds
            )
        except TimeoutError:
            pass


STAGES = [
    ("booking", "Booking"),
    ("precall", "Pre-call intelligence"),
    ("call", "Discovery call"),
    ("transcript", "Fathom transcript"),
    ("intelligence", "AI analysis"),
    ("growth_report", "Growth Intelligence Report"),
    ("strategy", "Strategy + share assets"),
    ("approval", "Diksha approval"),
    ("publish", "Notion + distribution"),
]

REVIEWABLE_ARTIFACTS = {
    "growth_autopsy",
    "linkedin_post",
    "strategy_doc",
    "pitch_deck_brief",
}

ARTIFACT_LABELS = {
    "precall_evidence": "Evidence pack",
    "precall_research": "Pre-call brief",
    "founder_intelligence": "Founder Intelligence",
    "growth_autopsy": "Growth Intelligence Report",
    "linkedin_post": "LinkedIn Growth Autopsy post",
    "strategy_decision": "Strategy routing",
    "strategy_doc": "One-problem Strategy Doc",
    "pitch_deck_brief": "Pitch deck",
    "notion_package": "Notion package",
    "linkedin_publication": "LinkedIn publication",
}


def _stage_state(index: int, active: int) -> str:
    return "complete" if index < active else "active" if index == active else "pending"


def _appointment_payload(
    settings: Settings,
    store: WorkflowStore,
    appointment: Appointment,
    *,
    now: datetime | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    artifacts = [
        item
        for item in store.list_artifacts(appointment.calendar_event_id)
        if settings.linkedin_enabled or item.kind not in LINKEDIN_ARTIFACT_KINDS
    ]
    kinds = {item.kind: item for item in artifacts}
    recordings = store.list_recordings(appointment.calendar_event_id)
    delivery_at = appointment.start_at - timedelta(
        minutes=settings.precall_delivery_minutes
    )
    precall = kinds.get("precall_research")
    if precall and precall.status in {
        ArtifactStatus.READY,
        ArtifactStatus.APPROVED,
        ArtifactStatus.REVISION_REQUESTED,
    }:
        delivery_state = (
            "ready_on_time"
            if precall.updated_at is None or precall.updated_at <= delivery_at
            else "ready_late"
        )
    elif current >= delivery_at:
        delivery_state = "overdue"
    else:
        delivery_state = "scheduled"
    active = 0
    if "precall_research" in kinds and kinds["precall_research"].status in {
        ArtifactStatus.READY,
        ArtifactStatus.APPROVED,
        ArtifactStatus.REVISION_REQUESTED,
    }:
        active = 2
    elif appointment.status in {AppointmentStatus.RESEARCH_SCHEDULED, AppointmentStatus.ANALYSIS_RUNNING}:
        active = 1
    if recordings:
        active = max(active, 3)
    if any(item.analysis_run_id for item in recordings):
        active = max(active, 4)
    if "founder_intelligence" in kinds or appointment.status == AppointmentStatus.INTELLIGENCE_READY:
        active = max(active, 5)
    if any(kinds.get(kind) for kind in DELIVERABLES):
        active = max(active, 6)
    if appointment.status == AppointmentStatus.CONTENT_DRAFTED or any(
        item.kind in DELIVERABLES
        and item.status in {ArtifactStatus.READY, ArtifactStatus.APPROVED, ArtifactStatus.REVISION_REQUESTED}
        for item in artifacts
    ):
        active = max(active, 7)
    linkedin_should_publish = (
        settings.linkedin_enabled and settings.linkedin_publish_after_notion
    )
    linkedin_complete = (
        "linkedin_publication" in kinds
        and kinds["linkedin_publication"].source_id.startswith("urn:li:")
    )
    distribution_complete = (
        linkedin_complete
        if linkedin_should_publish
        else appointment.status == AppointmentStatus.PUBLISHED or "notion_package" in kinds
    )
    if distribution_complete:
        active = len(STAGES)
    if appointment.status == AppointmentStatus.CANCELLED:
        active = 0
    stages = [
        {"key": key, "label": label, "state": _stage_state(index, active)}
        for index, (key, label) in enumerate(STAGES)
    ]
    current_stage = next(
        (stage for stage in stages if stage["state"] == "active"),
        stages[-1] if active >= len(STAGES) else stages[0],
    )
    strategy_intent = store.get_setting(
        f"strategy_intent:{appointment.calendar_event_id}"
    ) or appointment.strategy_mode
    founder_intelligence = kinds.get("founder_intelligence")
    service_lane = classify_service_lane(
        founder_intelligence.content if founder_intelligence else ""
    )
    approval_items = [
        item
        for item in artifacts
        if item.kind in REVIEWABLE_ARTIFACTS and item.status == ArtifactStatus.READY
    ]
    routing = kinds.get("strategy_decision")
    failed_artifacts = [item for item in artifacts if item.status == ArtifactStatus.FAILED]
    linkedin_verification = next(
        (
            item
            for item in artifacts
            if item.kind == "linkedin_publication"
            and item.status == ArtifactStatus.REVISION_REQUESTED
        ),
        None,
    )
    required_kinds: list[str] = []
    if "growth_autopsy" in kinds or strategy_intent in {
        "case_study_only",
        "strategy_requested",
    }:
        required_kinds.append("growth_autopsy")
        if settings.linkedin_enabled:
            required_kinds.append("linkedin_post")
    if strategy_intent == "strategy_requested":
        required_kinds.extend(["strategy_doc", "pitch_deck_brief"])
    approved_count = sum(
        kinds.get(kind) is not None and kinds[kind].status == ArtifactStatus.APPROVED
        for kind in required_kinds
    )
    reasons: list[str] = []
    if appointment.status == AppointmentStatus.NEEDS_INPUT:
        reasons.append("Calendar event needs a company and website")
    if appointment.status == AppointmentStatus.FAILED or appointment.last_error:
        reasons.append(appointment.last_error or "Workflow failed")
    if delivery_state == "overdue" and current < appointment.start_at:
        reasons.append("Pre-call brief missed the T-30 delivery target")
    if approval_items:
        reasons.append(f"{len(approval_items)} document(s) awaiting approval")
    if routing and routing.status == ArtifactStatus.READY:
        reasons.append("Strategy route requires Diksha's decision")
    if failed_artifacts:
        reasons.append(f"{len(failed_artifacts)} artifact(s) failed")
    if linkedin_verification:
        reasons.append(linkedin_verification.notes or "LinkedIn publication needs verification")

    if appointment.status == AppointmentStatus.CANCELLED:
        next_action = "No action — call cancelled"
    elif appointment.status == AppointmentStatus.NEEDS_INPUT:
        next_action = "Add the company and website to Calendar"
    elif appointment.status == AppointmentStatus.FAILED or failed_artifacts:
        next_action = "Review the error and retry the failed step"
    elif linkedin_verification:
        next_action = "Check LinkedIn, then record the post or approve one retry"
    elif routing and routing.status == ArtifactStatus.READY:
        next_action = "Choose Growth report only or create strategy + deck"
    elif approval_items:
        next_action = f"Review {len(approval_items)} ready document(s)"
    elif any(item.status == ArtifactStatus.PROCESSING for item in artifacts):
        next_action = "Automation is processing the next document"
    elif appointment.status == AppointmentStatus.PUBLISHED and distribution_complete:
        next_action = (
            "Package complete in Notion and LinkedIn"
            if settings.linkedin_enabled
            else "Package complete in Notion"
        )
    elif required_kinds and approved_count == len(required_kinds):
        next_action = (
            "Publish the approved post to LinkedIn"
            if "notion_package" in kinds and linkedin_should_publish
            else "Publish the approved package to Notion"
        )
    elif current < appointment.start_at and precall is None:
        next_action = "Wait for scheduled pre-call research"
    elif current < appointment.start_at:
        next_action = "Prepare for the discovery call"
    elif not recordings:
        next_action = "Waiting for the matched Fathom transcript"
    else:
        next_action = "Automation is waiting for the next trigger"

    recordings_payload = []
    if include_details:
        for recording in recordings:
            duration_seconds = None
            if recording.recording_start_at and recording.recording_end_at:
                duration_seconds = max(
                    0,
                    round(
                        (
                            recording.recording_end_at
                            - recording.recording_start_at
                        ).total_seconds()
                    ),
                )
            recordings_payload.append(
                {
                    "recording_id": recording.recording_id,
                    "status": recording.status.value,
                    "scheduled_start_at": recording.scheduled_start_at.isoformat(),
                    "recording_start_at": _iso(recording.recording_start_at),
                    "recording_end_at": _iso(recording.recording_end_at),
                    "duration_seconds": duration_seconds,
                    "external_invitee_count": len(recording.external_invitee_emails),
                    "transcript_available": bool(recording.transcript_path),
                    "fathom_url": str(
                        recording.payload.get("share_url")
                        or recording.payload.get("url")
                        or ""
                    ),
                    "last_error": recording.last_error,
                }
            )
    return {
        "calendar_event_id": appointment.calendar_event_id,
        "title": appointment.title,
        "company": appointment.company,
        "website": appointment.website,
        "founder_name": appointment.founder_name,
        "founder_email": appointment.founder_email,
        "founder_linkedin": appointment.founder_linkedin,
        "industry": appointment.industry,
        "meeting_agenda": appointment.meeting_agenda,
        "strategy_mode": appointment.strategy_mode,
        "strategy_intent": strategy_intent,
        "service_lane": service_lane,
        "conference_url": calendar_conference_url(appointment.source_payload),
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "status": appointment.status.value,
        "last_error": appointment.last_error,
        "research_start_at": _iso(appointment.research_start_at),
        "precall_delivery_at": delivery_at.isoformat(),
        "precall_delivery_state": delivery_state,
        "current_stage": current_stage,
        "next_action": next_action,
        "attention_reasons": reasons,
        "needs_attention": bool(reasons),
        "approval": {
            "required": len(required_kinds),
            "approved": approved_count,
            "awaiting_review": len(approval_items),
        },
        "recording_count": len(recordings),
        "latest_recording_status": recordings[0].status.value if recordings else None,
        "precall_can_run": bool(
            appointment.website
            and appointment.status != AppointmentStatus.CANCELLED
            and (
                "precall_research" not in kinds
                or kinds["precall_research"].status != ArtifactStatus.PROCESSING
            )
        ),
        "progress": round(min(active, len(STAGES)) / len(STAGES) * 100),
        "stages": stages,
        "artifacts": [_artifact_payload(item) for item in artifacts],
        "recordings": recordings_payload,
        "timeline": (
            store.list_workflow_events(appointment.calendar_event_id, limit=100)
            if include_details
            else []
        ),
    }


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    if artifact.kind == "strategy_decision" and artifact.status == ArtifactStatus.READY:
        action = "route"
    elif artifact.kind in REVIEWABLE_ARTIFACTS and artifact.status == ArtifactStatus.READY:
        action = "review"
    elif (
        artifact.kind == "linkedin_publication"
        and artifact.status == ArtifactStatus.REVISION_REQUESTED
    ):
        action = "verify_linkedin"
    elif artifact.kind == "linkedin_publication" and artifact.status == ArtifactStatus.FAILED:
        action = "publish_retry"
    elif artifact.status == ArtifactStatus.FAILED:
        action = "retry"
    elif artifact.status == ArtifactStatus.REVISION_REQUESTED:
        action = "revision"
    else:
        action = "none"
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "label": ARTIFACT_LABELS.get(artifact.kind, artifact.kind.replace("_", " ").title()),
        "title": artifact.title,
        "status": artifact.status.value,
        "source_id": artifact.source_id,
        "filename": Path(artifact.file_path).name if artifact.file_path else "",
        "has_file": bool(artifact.file_path),
        "notes": artifact.notes,
        "external_url": (
            artifact.content
            if artifact.kind in {"notion_package", "linkedin_publication"}
            else ""
        ),
        "created_at": _iso(artifact.created_at),
        "updated_at": _iso(artifact.updated_at),
        "action_required": action,
    }


def appointment_detail_payload(
    settings: Settings,
    store: WorkflowStore,
    calendar_event_id: str,
) -> dict[str, Any] | None:
    appointment = store.get_appointment(calendar_event_id)
    if appointment is None:
        return None
    return _appointment_payload(
        settings,
        store,
        appointment,
        now=datetime.now(UTC),
        include_details=True,
    )


def _integration_state(configured: bool, *, error: str | None = None) -> str:
    if error:
        return "attention"
    return "configured" if configured else "not_configured"


def dashboard_payload(settings: Settings, store: WorkflowStore) -> dict[str, Any]:
    appointments = store.list_appointments(limit=500)
    now = datetime.now(UTC)
    payloads = [
        _appointment_payload(settings, store, item, now=now) for item in appointments
    ]
    attention = sum(item["needs_attention"] for item in payloads)
    awaiting_approval = sum(item["approval"]["awaiting_review"] for item in payloads)
    routing_decisions = sum(
        any(artifact["action_required"] == "route" for artifact in item["artifacts"])
        for item in payloads
    )
    stage_counts = {key: 0 for key, _ in STAGES}
    for item in payloads:
        stage_counts[item["current_stage"]["key"]] += 1
    companies = {item["calendar_event_id"]: item["company"] for item in payloads}
    recent_activity = store.list_workflow_events(limit=12)
    for event in recent_activity:
        event["company"] = companies.get(event["calendar_event_id"], "Unknown company")
    calendar_error = store.get_setting("calendar_last_error") or None
    linkedin_auth = LinkedInTokenStore(settings.linkedin_token_file).inspect()
    integrations = [
        {
            "key": "database",
            "label": "SQL database",
            "state": "connected",
            "detail": "SQLite · WAL mode",
        },
        {
            "key": "calendar",
            "label": "Google Calendar",
            "state": _integration_state(settings.google_token_file.exists(), error=calendar_error),
            "detail": calendar_error or (
                "OAuth token configured" if settings.google_token_file.exists() else "OAuth token missing"
            ),
        },
        {
            "key": "ai",
            "label": "AI synthesis",
            "state": _integration_state(
                bool(settings.ai_api_key and settings.ai_model)
            ),
            "detail": (
                f"Direct model: {settings.ai_model}"
                if settings.ai_api_key and settings.ai_model
                else "API key or model missing"
            ),
        },
        {
            "key": "fathom",
            "label": "Fathom",
            "state": _integration_state(bool(settings.fathom_webhook_secret)),
            "detail": (
                "Webhook + API fallback configured"
                if settings.fathom_webhook_secret and settings.fathom_api_key
                else "Webhook configured" if settings.fathom_webhook_secret else "Webhook secret missing"
            ),
        },
        {
            "key": "notion",
            "label": "Notion",
            "state": _integration_state(
                bool(settings.notion_api_key and settings.notion_parent_page_id)
            ),
            "detail": (
                "Private page publishing configured"
                if settings.notion_api_key and settings.notion_parent_page_id
                else "Connection or parent page missing"
            ),
        },
        {
            "key": "linkedin",
            "label": "LinkedIn",
            "state": (
                _integration_state(
                    linkedin_auth["authorized"] and not linkedin_auth["expired"]
                )
                if settings.linkedin_enabled
                else "disabled"
            ),
            "detail": (
                "Temporarily paused; Notion is the final publishing step"
                if not settings.linkedin_enabled
                else "Personal profile authorized"
                if linkedin_auth["authorized"] and not linkedin_auth["expired"]
                else "Connect a personal profile in Admin"
            ),
        },
        {
            "key": "browser",
            "label": "Playwright",
            "state": "enabled" if settings.playwright_enabled else "disabled",
            "detail": "Chromium public research renderer",
        },
        {
            "key": "semrush",
            "label": "Semrush MCP",
            "state": (
                "configured"
                if settings.semrush_mcp_enabled and settings.semrush_api_key
                else "optional"
            ),
            "detail": (
                f"Direct MCP · max {settings.semrush_mcp_max_reports} reports"
                if settings.semrush_mcp_enabled and settings.semrush_api_key
                else "Optional subscription/unit-metered enrichment"
            ),
        },
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "system": {
            "calendar_last_sync_at": store.get_setting("calendar_last_sync_at"),
            "calendar_last_error": calendar_error,
            "automation_last_tick_at": store.get_setting("automation_last_tick_at"),
            "research_backend": settings.precall_research_backend,
            "environment": settings.environment,
            "background_sync_enabled": settings.enable_background_sync,
            "sync_interval_seconds": settings.background_sync_interval_seconds,
            "integrations": integrations,
            "database_counts": store.database_counts(),
            "free_collectors": [
                "Playwright-rendered website crawl",
                "robots.txt + sitemap",
                "DuckDuckGo public search",
                "Google PageSpeed / local Lighthouse",
                "On-page SEO + technology signals",
                "Social + marketplace footprint",
                "Ad transparency discovery",
                "Optional licensed Semrush MCP enrichment",
            ],
        },
        "metrics": {
            "appointments": len(payloads),
            "active": sum(
                item["status"] not in {
                    AppointmentStatus.CANCELLED.value,
                    AppointmentStatus.PUBLISHED.value,
                }
                for item in payloads
            ),
            "needs_attention": attention,
            "awaiting_approval": awaiting_approval,
            "routing_decisions": routing_decisions,
            "sla_at_risk": sum(
                item["precall_delivery_state"] == "overdue"
                and datetime.fromisoformat(item["start_at"]) > now
                for item in payloads
            ),
            "published": sum(
                item["status"] == AppointmentStatus.PUBLISHED.value for item in payloads
            ),
            "failed": sum(
                item["status"] == AppointmentStatus.FAILED.value for item in payloads
            ),
            "today": sum(
                datetime.fromisoformat(item["start_at"]).astimezone().date()
                == now.astimezone().date()
                for item in payloads
            ),
        },
        "pipeline_counts": stage_counts,
        "recent_activity": recent_activity,
        "appointments": payloads,
    }
