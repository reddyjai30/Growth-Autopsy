from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .controller import (
    _sync_message,
    automation_loop,
    collect_agent_outputs,
    dashboard_payload,
    record_sync_error,
    run_due_precall_research,
    sync_calendar_once,
)
from .domain import ArtifactStatus, AppointmentStatus
from .fathom import FathomIngestionService, FathomWebhookError
from .hermes import HermesClient, HermesError
from .store import WorkflowStore


@lru_cache(maxsize=1)
def get_store() -> WorkflowStore:
    settings = get_settings()
    store = WorkflowStore(settings.database_path)
    store.initialize()
    return store


def get_hermes(settings: Settings = Depends(get_settings)) -> HermesClient:
    return HermesClient(
        settings.hermes_base_url,
        settings.hermes_api_key,
        delivery_target=settings.hermes_delivery_target,
    )


def require_internal_key(
    x_internal_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.internal_api_key and x_internal_key != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid key")


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = get_store()
    store.initialize()
    settings = get_settings()
    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.enable_background_sync:
        task = asyncio.create_task(
            automation_loop(settings, store, get_hermes(settings), stop),
            name="growth-autopsy-automation",
        )
    yield
    if task:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()


app = FastAPI(title="Growth Autopsy POC", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.2.0"}


@app.post("/webhooks/fathom", status_code=status.HTTP_202_ACCEPTED)
async def fathom_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    hermes: HermesClient = Depends(get_hermes),
) -> dict:
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > settings.max_fathom_webhook_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    raw_body = await request.body()
    if len(raw_body) > settings.max_fathom_webhook_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    service = FathomIngestionService(
        store,
        hermes,
        webhook_secret=settings.fathom_webhook_secret,
        transcript_dir=settings.shared_workdir / "transcripts",
        match_window_minutes=settings.fathom_match_window_minutes,
    )
    try:
        result = await service.ingest(raw_body, request.headers)
    except FathomWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except (HermesError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {
        "status": result.status,
        "webhook_id": result.webhook_id,
        "recording_id": result.recording_id,
        "calendar_event_id": result.calendar_event_id,
        "analysis_run_id": result.analysis_run_id,
    }


@app.post("/internal/calendar/sync", dependencies=[Depends(require_internal_key)])
async def calendar_sync(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    hermes: HermesClient = Depends(get_hermes),
) -> dict:
    try:
        result = await sync_calendar_once(settings, store)
        _sync_message(store, result)
        research = await run_due_precall_research(settings, store, hermes)
        outputs = await collect_agent_outputs(settings, store, hermes)
    except Exception as exc:
        record_sync_error(store, exc)
        raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc
    return {**result, "research": research, "agent_outputs": outputs}


@app.get("/internal/dashboard", dependencies=[Depends(require_internal_key)])
async def dashboard(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    return dashboard_payload(settings, store)


@app.get("/internal/appointments", dependencies=[Depends(require_internal_key)])
async def appointments(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> list[dict]:
    return dashboard_payload(settings, store)["appointments"]


@app.post(
    "/internal/appointments/{calendar_event_id}/precall/run",
    dependencies=[Depends(require_internal_key)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_precall_now(
    calendar_event_id: str,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    hermes: HermesClient = Depends(get_hermes),
) -> dict:
    appointment = store.get_appointment(calendar_event_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appointment.status == AppointmentStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="Cancelled appointments cannot run")
    if not appointment.website:
        raise HTTPException(status_code=409, detail="Add the company website to the calendar event")
    artifact = store.get_artifact_by_kind(calendar_event_id, "precall_research")
    if artifact and artifact.status == ArtifactStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Pre-call research is already running")
    result = await run_due_precall_research(
        settings,
        store,
        hermes,
        force_event_id=calendar_event_id,
    )
    if not result:
        raise HTTPException(status_code=409, detail="Pre-call research could not be queued")
    return result[0]


class DecisionRequest(BaseModel):
    decision: str
    notes: str = Field(default="", max_length=4000)


@app.post("/internal/artifacts/{artifact_id}/decision", dependencies=[Depends(require_internal_key)])
async def artifact_decision(
    artifact_id: int,
    decision: DecisionRequest,
    store: WorkflowStore = Depends(get_store),
) -> dict[str, str | int]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.status not in {
        ArtifactStatus.READY,
        ArtifactStatus.APPROVED,
        ArtifactStatus.REVISION_REQUESTED,
    }:
        raise HTTPException(status_code=409, detail="Artifact is not ready for a decision")
    normalized = decision.decision.casefold()
    if normalized == "approve":
        new_status = ArtifactStatus.APPROVED
    elif normalized in {"revise", "revision", "reject"}:
        if not decision.notes.strip():
            raise HTTPException(status_code=422, detail="Revision notes are required")
        new_status = ArtifactStatus.REVISION_REQUESTED
    else:
        raise HTTPException(status_code=422, detail="Decision must be approve or revise")
    store.update_artifact(artifact_id, status=new_status, notes=decision.notes.strip())
    return {"artifact_id": artifact_id, "status": new_status.value}


@app.get("/internal/artifacts/{artifact_id}/download", dependencies=[Depends(require_internal_key)])
async def artifact_download(
    artifact_id: int,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> FileResponse:
    artifact = store.get_artifact(artifact_id)
    if artifact is None or not artifact.file_path:
        raise HTTPException(status_code=404, detail="Artifact file not found")
    target = Path(artifact.file_path).resolve()
    allowed = settings.shared_workdir.resolve()
    if not target.is_relative_to(allowed) or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(target, filename=target.name, media_type="text/markdown")


STATIC_DIR = Path(__file__).with_name("dashboard")
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
