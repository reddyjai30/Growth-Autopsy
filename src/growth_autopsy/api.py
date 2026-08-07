from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import AIClient
from .config import Settings, get_settings
from .controller import (
    _sync_message,
    appointment_detail_payload,
    automation_loop,
    dashboard_payload,
    publish_approved_package,
    record_sync_error,
    resolve_strategy_decision_direct,
    revise_postcall_artifact_direct,
    run_due_precall_research,
    sync_calendar_once,
)
from .domain import ArtifactStatus, AppointmentStatus
from .documents import (
    document_filename,
    render_document_html,
    resolve_artifact_source,
)
from .fathom import FathomIngestionService, FathomWebhookError
from .store import WorkflowStore


@lru_cache(maxsize=1)
def get_store() -> WorkflowStore:
    settings = get_settings()
    store = WorkflowStore(settings.database_path)
    store.initialize()
    return store


def get_ai(settings: Settings = Depends(get_settings)) -> AIClient:
    return AIClient(
        settings.ai_base_url,
        settings.ai_api_key,
        settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = get_store()
    store.initialize()
    settings = get_settings()
    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.enable_background_sync:
        task = asyncio.create_task(
            automation_loop(settings, store, get_ai(settings), stop),
            name="growth-autopsy-automation",
        )
    yield
    if task:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()


app = FastAPI(title="Growth Autopsy", version="0.4.0", lifespan=lifespan)
_pdf_render_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.4.0"}


@app.post("/webhooks/fathom", status_code=status.HTTP_202_ACCEPTED)
async def fathom_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > settings.max_fathom_webhook_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    raw_body = await request.body()
    if len(raw_body) > settings.max_fathom_webhook_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    service = FathomIngestionService(
        store,
        webhook_secret=settings.fathom_webhook_secret,
        transcript_dir=settings.shared_workdir / "transcripts",
        match_window_minutes=settings.fathom_match_window_minutes,
        api_key=settings.fathom_api_key,
    )
    try:
        result = await service.ingest(raw_body, request.headers)
    except FathomWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {
        "status": result.status,
        "webhook_id": result.webhook_id,
        "recording_id": result.recording_id,
        "calendar_event_id": result.calendar_event_id,
        "analysis_run_id": result.analysis_run_id,
    }


@app.post("/internal/calendar/sync")
async def calendar_sync(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    ai: AIClient = Depends(get_ai),
) -> dict:
    try:
        result = await sync_calendar_once(settings, store)
        _sync_message(store, result)
        research = await run_due_precall_research(settings, store, ai)
    except Exception as exc:
        record_sync_error(store, exc)
        raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc
    return {**result, "research": research}


@app.get("/internal/dashboard")
async def dashboard(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    return dashboard_payload(settings, store)


@app.get("/internal/appointments")
async def appointments(
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> list[dict]:
    return dashboard_payload(settings, store)["appointments"]


@app.get("/internal/appointments/{calendar_event_id}")
async def appointment_detail(
    calendar_event_id: str,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    payload = appointment_detail_payload(settings, store, calendar_event_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return payload


@app.delete("/internal/appointments/{calendar_event_id}")
async def delete_appointment(
    calendar_event_id: str,
    store: WorkflowStore = Depends(get_store),
) -> dict[str, str]:
    if not store.delete_appointment(calendar_event_id):
        raise HTTPException(status_code=404, detail="Appointment not found")
    return {
        "status": "deleted",
        "calendar_event_id": calendar_event_id,
        "calendar_event": "unchanged",
    }


@app.post(
    "/internal/appointments/{calendar_event_id}/precall/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_precall_now(
    calendar_event_id: str,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    ai: AIClient = Depends(get_ai),
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
        ai,
        force_event_id=calendar_event_id,
    )
    if not result:
        raise HTTPException(status_code=409, detail="Pre-call research could not be queued")
    return result[0]


class DecisionRequest(BaseModel):
    decision: str
    notes: str = Field(default="", max_length=4000)


@app.post("/internal/artifacts/{artifact_id}/decision")
async def artifact_decision(
    artifact_id: int,
    decision: DecisionRequest,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    ai: AIClient = Depends(get_ai),
) -> dict:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.kind == "strategy_decision":
        raise HTTPException(
            status_code=409,
            detail="Use the strategy-decision endpoint to select the route",
        )
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
    if new_status == ArtifactStatus.REVISION_REQUESTED and artifact.kind in {
        "growth_autopsy",
        "strategy_doc",
        "pitch_deck_brief",
    }:
        try:
            return await revise_postcall_artifact_direct(
                settings,
                store,
                ai,
                artifact,
                decision.notes.strip(),
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.update_artifact(artifact_id, status=new_status, notes=decision.notes.strip())
    publication = None
    if new_status == ArtifactStatus.APPROVED and settings.notion_publish_after_approval:
        try:
            publication = await publish_approved_package(
                settings, store, artifact.calendar_event_id
            )
        except Exception as exc:
            publication = {"status": "failed", "error": str(exc)[:1000]}
    return {
        "artifact_id": artifact_id,
        "status": new_status.value,
        "publication": publication,
    }


class StrategyDecisionRequest(BaseModel):
    intent: str


@app.post(
    "/internal/appointments/{calendar_event_id}/strategy-decision",
)
async def strategy_decision(
    calendar_event_id: str,
    request: StrategyDecisionRequest,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    ai: AIClient = Depends(get_ai),
) -> dict:
    intent = request.intent.strip().casefold()
    try:
        queued = await resolve_strategy_decision_direct(
            settings, store, ai, calendar_event_id, intent
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"calendar_event_id": calendar_event_id, "intent": intent, "queued": queued}


@app.post(
    "/internal/appointments/{calendar_event_id}/notion/publish",
)
async def notion_publish(
    calendar_event_id: str,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    try:
        return await publish_approved_package(settings, store, calendar_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc


def _artifact_document(
    artifact_id: int,
    settings: Settings,
    store: WorkflowStore,
) -> tuple[Path, str, str, str]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None or not artifact.file_path:
        raise HTTPException(status_code=404, detail="Artifact file not found")
    try:
        target = resolve_artifact_source(artifact.file_path, settings.shared_workdir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact file not found") from exc
    if target.stat().st_size > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Artifact is too large to render")
    appointment = store.get_appointment(artifact.calendar_event_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Artifact file not found")
    try:
        markdown = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail="Artifact is not valid UTF-8 text") from exc
    labels = {
        "precall_research": "Pre-call Research",
        "founder_intelligence": "Founder Intelligence",
        "growth_autopsy": "Growth Autopsy",
        "strategy_doc": "90-day Strategy",
        "pitch_deck_brief": "Pitch Deck Brief",
    }
    label = labels.get(artifact.kind, artifact.kind.replace("_", " ").title())
    title = artifact.title.strip() or f"{appointment.company} — {label}"
    rendered = render_document_html(
        markdown,
        title=title,
        company=appointment.company,
        label=label,
        generated_at=artifact.updated_at or artifact.created_at,
    )
    filename = document_filename(appointment.company, label, "pdf")
    return target, rendered, filename, appointment.company


@app.get(
    "/internal/artifacts/{artifact_id}/view",
    response_class=HTMLResponse,
)
async def artifact_view(
    artifact_id: int,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> HTMLResponse:
    _, rendered, _, _ = _artifact_document(artifact_id, settings, store)
    return HTMLResponse(
        rendered,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/internal/artifacts/{artifact_id}/download")
async def artifact_download(
    artifact_id: int,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> FileResponse:
    source, rendered, filename, _ = _artifact_document(artifact_id, settings, store)
    export_dir = (settings.shared_workdir / "exports").resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = export_dir / f"artifact-{artifact_id}.pdf"

    async with _pdf_render_lock:
        source_mtime = source.stat().st_mtime_ns
        if not pdf_path.is_file() or pdf_path.stat().st_mtime_ns < source_mtime:
            try:
                from playwright.async_api import async_playwright

                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    try:
                        page = await browser.new_page()
                        await page.set_content(rendered, wait_until="load")
                        pdf = await page.pdf(
                            format="A4",
                            print_background=True,
                            margin={
                                "top": "16mm",
                                "right": "15mm",
                                "bottom": "17mm",
                                "left": "15mm",
                            },
                        )
                    finally:
                        await browser.close()
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "PDF renderer is unavailable. Run "
                        "`uv run playwright install chromium` and try again."
                    ),
                ) from exc
            temporary = pdf_path.with_suffix(".pdf.tmp")
            temporary.write_bytes(pdf)
            temporary.replace(pdf_path)

    return FileResponse(pdf_path, filename=filename, media_type="application/pdf")


STATIC_DIR = Path(__file__).with_name("dashboard")
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
