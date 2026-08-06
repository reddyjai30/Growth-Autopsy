from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .calendar_ingestion import CalendarIngestionService, GoogleCalendarGateway
from .config import Settings
from .domain import Artifact, ArtifactStatus, Appointment, AppointmentStatus, RecordingStatus
from .hermes import HermesClient
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
    hermes: HermesClient,
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
        run_id = await hermes.start_precall_run(appointment, evidence)
        store.update_artifact(
            artifact.id,
            status=ArtifactStatus.PROCESSING,
            source_id=f"run:{run_id}",
            notes="Evidence collected; Gemini synthesis is running in Hermes",
        )
        return {
            "event_id": appointment.calendar_event_id,
            "status": "synthesis_started",
            "run_id": run_id,
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
    hermes: HermesClient,
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
                settings, store, hermes, item[0], item[1], researcher=researcher
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
        store.mark_appointment_status(event_id, AppointmentStatus.CONTENT_DRAFTED)
        return {"recording_id": recording_id, "status": "ready"}
    if run_status in {"failed", "error", "cancelled", "canceled"}:
        message = error or f"Hermes post-call run ended with status {run_status}"
        store.mark_recording_status(recording_id, RecordingStatus.FAILED, message)
        store.mark_appointment_status(event_id, AppointmentStatus.FAILED, message)
        return {"recording_id": recording_id, "status": "failed", "error": message}
    return {"recording_id": recording_id, "status": run_status or "running"}


async def collect_agent_outputs(
    settings: Settings, store: WorkflowStore, hermes: HermesClient
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for artifact in store.list_artifacts(statuses=(ArtifactStatus.PROCESSING,)):
        if not artifact.source_id.startswith("run:"):
            continue
        try:
            results.append(await _collect_precall_run(settings, store, hermes, artifact))
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


async def automation_loop(
    settings: Settings,
    store: WorkflowStore,
    hermes: HermesClient,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            sync_result = await sync_calendar_once(settings, store)
            _sync_message(store, sync_result)
        except Exception as exc:
            record_sync_error(store, exc)
        try:
            await run_due_precall_research(settings, store, hermes)
            await collect_agent_outputs(settings, store, hermes)
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
    ("case_study", "Growth autopsy"),
    ("strategy", "Strategy + deck"),
    ("approval", "Diksha approval"),
    ("publish", "Notion + distribution"),
]


def _stage_state(index: int, active: int) -> str:
    return "complete" if index < active else "active" if index == active else "pending"


def _appointment_payload(store: WorkflowStore, appointment: Appointment) -> dict[str, Any]:
    artifacts = store.list_artifacts(appointment.calendar_event_id)
    kinds = {item.kind: item for item in artifacts}
    recordings = store.list_recordings(appointment.calendar_event_id)
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
    if "founder_intelligence" in kinds:
        active = max(active, 5)
    if appointment.status == AppointmentStatus.CONTENT_DRAFTED:
        active = max(active, 6)
    if any(item.status in {ArtifactStatus.APPROVED, ArtifactStatus.REVISION_REQUESTED} for item in artifacts):
        active = max(active, 7)
    if appointment.status == AppointmentStatus.CANCELLED:
        active = 0
    return {
        "calendar_event_id": appointment.calendar_event_id,
        "title": appointment.title,
        "company": appointment.company,
        "website": appointment.website,
        "founder_name": appointment.founder_name,
        "founder_email": appointment.founder_email,
        "founder_linkedin": appointment.founder_linkedin,
        "industry": appointment.industry,
        "strategy_mode": appointment.strategy_mode,
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "status": appointment.status.value,
        "last_error": appointment.last_error,
        "research_start_at": _iso(appointment.research_start_at),
        "precall_can_run": bool(
            appointment.website
            and appointment.status != AppointmentStatus.CANCELLED
            and (
                "precall_research" not in kinds
                or kinds["precall_research"].status != ArtifactStatus.PROCESSING
            )
        ),
        "progress": round(min(active, len(STAGES)) / len(STAGES) * 100),
        "stages": [
            {"key": key, "label": label, "state": _stage_state(index, active)}
            for index, (key, label) in enumerate(STAGES)
        ],
        "artifacts": [_artifact_payload(item) for item in artifacts],
    }


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "title": artifact.title,
        "status": artifact.status.value,
        "source_id": artifact.source_id,
        "filename": Path(artifact.file_path).name if artifact.file_path else "",
        "has_file": bool(artifact.file_path),
        "notes": artifact.notes,
        "created_at": _iso(artifact.created_at),
        "updated_at": _iso(artifact.updated_at),
    }


def dashboard_payload(settings: Settings, store: WorkflowStore) -> dict[str, Any]:
    appointments = store.list_appointments(limit=200)
    payloads = [_appointment_payload(store, item) for item in appointments]
    attention = sum(
        item["status"] in {AppointmentStatus.NEEDS_INPUT.value, AppointmentStatus.FAILED.value}
        or any(
            artifact["status"] in {
                ArtifactStatus.READY.value,
                ArtifactStatus.REVISION_REQUESTED.value,
                ArtifactStatus.FAILED.value,
            }
            for artifact in item["artifacts"]
        )
        for item in payloads
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "system": {
            "calendar_last_sync_at": store.get_setting("calendar_last_sync_at"),
            "calendar_last_error": store.get_setting("calendar_last_error") or None,
            "automation_last_tick_at": store.get_setting("automation_last_tick_at"),
            "research_backend": settings.precall_research_backend,
            "free_collectors": [
                "Website crawl",
                "robots.txt + sitemap",
                "DuckDuckGo public search",
                "Google PageSpeed / local Lighthouse",
                "On-page SEO + technology signals",
            ],
        },
        "metrics": {
            "appointments": len(payloads),
            "active": sum(
                item["status"] not in {
                    AppointmentStatus.CANCELLED.value,
                    AppointmentStatus.CONTENT_DRAFTED.value,
                }
                for item in payloads
            ),
            "needs_attention": attention,
        },
        "appointments": payloads,
    }
