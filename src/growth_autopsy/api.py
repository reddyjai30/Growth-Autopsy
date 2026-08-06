from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from .calendar_ingestion import CalendarIngestionService, GoogleCalendarGateway
from .config import Settings, get_settings
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
    get_store().initialize()
    yield


app = FastAPI(title="Growth Autopsy POC", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
    gateway = GoogleCalendarGateway(
        settings.google_token_file,
        settings.google_calendar_id,
        lookback_hours=settings.calendar_lookback_hours,
        lookahead_days=settings.calendar_lookahead_days,
    )
    service = CalendarIngestionService(
        gateway,
        store,
        hermes,
        calendar_id=settings.google_calendar_id,
        title_prefix=settings.calendar_title_prefix,
        diksha_email=settings.diksha_email,
        precall_start_minutes=settings.precall_start_minutes,
        precall_delivery_minutes=settings.precall_delivery_minutes,
    )
    result = await service.sync()
    return {
        "scanned": result.scanned,
        "ignored": result.ignored,
        "scheduled": result.scheduled,
        "updated": result.updated,
        "cancelled": result.cancelled,
        "needs_input": result.needs_input,
    }


@app.get("/internal/appointments", dependencies=[Depends(require_internal_key)])
async def appointments(store: WorkflowStore = Depends(get_store)) -> list[dict]:
    return [
        {
            "calendar_event_id": item.calendar_event_id,
            "title": item.title,
            "company": item.company,
            "website": item.website,
            "start_at": item.start_at.isoformat(),
            "status": item.status.value,
            "research_job_id": item.research_job_id,
            "last_error": item.last_error,
        }
        for item in store.list_appointments()
    ]

