from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin as admin_service
from .ai import AIClient
from .config import Settings, get_settings
from .controller import (
    _sync_message,
    appointment_detail_payload,
    automation_loop,
    dashboard_payload,
    generate_dependent_deliverable_direct,
    publish_approved_distribution,
    record_sync_error,
    resolve_linkedin_publication_uncertainty,
    resolve_strategy_decision_direct,
    retry_postcall_artifact_direct,
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
from .linkedin import LinkedInAuthorizationError, LinkedInClient, LinkedInTokenStore
from .security import (
    PUBLIC_PATHS,
    SESSION_COOKIE,
    create_session_cookie,
    credentials_are_valid,
    production_access_enabled,
    safe_next_path,
    session_is_valid,
    validate_production_access,
)
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
    validate_production_access(settings)
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


app = FastAPI(title="Growth Autopsy", version="0.6.0", lifespan=lifespan)
_pdf_render_lock = asyncio.Lock()


@app.middleware("http")
async def operator_access_and_security_headers(request: Request, call_next):
    settings = get_settings()
    production = production_access_enabled(settings)
    request.state.operator_authenticated = False
    if production and request.url.path not in PUBLIC_PATHS:
        session = request.cookies.get(SESSION_COOKIE, "")
        if not session_is_valid(session, settings):
            if request.method in {"GET", "HEAD"} and not request.url.path.startswith(
                "/internal/"
            ):
                next_path = request.url.path
                if request.url.query:
                    next_path = f"{next_path}?{request.url.query}"
                response: Response = RedirectResponse(
                    f"/login?{urlencode({'next': next_path})}",
                    status_code=303,
                )
            else:
                response = JSONResponse(
                    {"detail": "Operator authentication required"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            _set_security_headers(response, production=True, private=True)
            return response
        request.state.operator_authenticated = True

    response = await call_next(request)
    private = production or request.url.path.startswith(
        ("/internal/admin", "/internal/linkedin/oauth")
    )
    _set_security_headers(response, production=production, private=private)
    return response


def _set_security_headers(
    response: Response,
    *,
    production: bool,
    private: bool,
) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'"
    )
    if private:
        response.headers["Cache-Control"] = "private, no-store"
    if production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )


def _login_page(*, next_path: str = "/", error: str = "") -> str:
    error_html = (
        f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light" />
    <title>Growth Autopsy — Sign in</title>
    <style>
      :root {{ font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #11281f; background: #eef4ef; }}
      * {{ box-sizing: border-box; }}
      body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 24px; }}
      main {{ width: min(430px, 100%); background: #fff; border: 1px solid #dce7df; border-radius: 24px; padding: 36px; box-shadow: 0 24px 70px rgba(20, 55, 40, .12); }}
      .mark {{ display: inline-grid; grid-template-columns: repeat(3, 7px); gap: 4px; margin-bottom: 22px; }}
      .mark i {{ width: 7px; height: 25px; border-radius: 5px; background: #25a265; }}
      .mark i:nth-child(2) {{ height: 18px; margin-top: 7px; }}
      .mark i:nth-child(3) {{ height: 11px; margin-top: 14px; }}
      h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: -.03em; }}
      p {{ margin: 0 0 26px; color: #60726a; line-height: 1.55; }}
      label {{ display: grid; gap: 8px; margin-top: 18px; font-size: 13px; font-weight: 700; }}
      input {{ width: 100%; border: 1px solid #cbd9d0; border-radius: 12px; padding: 13px 14px; font: inherit; outline: none; }}
      input:focus {{ border-color: #23875a; box-shadow: 0 0 0 3px rgba(35, 135, 90, .13); }}
      button {{ width: 100%; margin-top: 24px; padding: 14px; border: 0; border-radius: 12px; background: #163d2d; color: #fff; font: inherit; font-weight: 750; cursor: pointer; }}
      .error {{ margin: 16px 0 0; padding: 11px 13px; border-radius: 10px; background: #fff0ef; color: #9a3028; font-size: 14px; }}
      small {{ display: block; margin-top: 22px; color: #839189; text-align: center; }}
    </style>
  </head>
  <body>
    <main>
      <span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <h1>Welcome back</h1>
      <p>Sign in to the private Growth Autopsy workspace.</p>
      {error_html}
      <form method="post" action="/login">
        <input type="hidden" name="next" value="{escape(next_path, quote=True)}" />
        <label>Username<input name="username" autocomplete="username" required autofocus /></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required /></label>
        <button type="submit">Sign in</button>
      </form>
      <small>Marketing Mosaics · Private operator access</small>
    </main>
  </body>
</html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = Query(default="/", max_length=2_000),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not production_access_enabled(settings):
        return RedirectResponse("/", status_code=303)
    destination = safe_next_path(next)
    if session_is_valid(request.cookies.get(SESSION_COOKIE, ""), settings):
        return RedirectResponse(destination, status_code=303)
    return HTMLResponse(_login_page(next_path=destination))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    if not production_access_enabled(settings):
        return RedirectResponse("/", status_code=303)
    raw_body = await request.body()
    if len(raw_body) > 16_384:
        raise HTTPException(status_code=413, detail="Login request is too large")
    try:
        values = parse_qs(
            raw_body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=5,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid login form") from exc
    username = values.get("username", [""])[0]
    password = values.get("password", [""])[0]
    destination = safe_next_path(values.get("next", ["/"])[0])
    if not credentials_are_valid(username, password, settings):
        return HTMLResponse(
            _login_page(
                next_path=destination,
                error="The username or password is incorrect.",
            ),
            status_code=401,
        )
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(settings),
        max_age=settings.session_ttl_hours * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.6.0"}


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
        "linkedin_post",
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
    dependent_generation = None
    if new_status == ArtifactStatus.APPROVED:
        approved_parent = store.get_artifact(artifact_id)
        if approved_parent is not None:
            try:
                dependent_generation = await generate_dependent_deliverable_direct(
                    settings,
                    store,
                    ai,
                    approved_parent,
                )
            except Exception as exc:
                dependent_generation = {"status": "failed", "error": str(exc)[:1000]}
    publication = None
    if new_status == ArtifactStatus.APPROVED and settings.notion_publish_after_approval:
        try:
            publication = await publish_approved_distribution(
                settings, store, artifact.calendar_event_id
            )
        except Exception as exc:
            publication = {"status": "failed", "error": str(exc)[:1000]}
    return {
        "artifact_id": artifact_id,
        "status": new_status.value,
        "dependent_generation": dependent_generation,
        "publication": publication,
    }


class StrategyDecisionRequest(BaseModel):
    intent: str


class LinkedInPublicationResolution(BaseModel):
    outcome: str = Field(max_length=20)
    post_url: str = Field(default="", max_length=2_000)


@app.post("/internal/artifacts/{artifact_id}/retry")
async def retry_artifact(
    artifact_id: int,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
    ai: AIClient = Depends(get_ai),
) -> dict:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        return await retry_postcall_artifact_direct(
            settings, store, ai, artifact
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
@app.post(
    "/internal/appointments/{calendar_event_id}/distribution/publish",
)
async def notion_publish(
    calendar_event_id: str,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict:
    try:
        return await publish_approved_distribution(settings, store, calendar_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc


@app.post(
    "/internal/appointments/{calendar_event_id}/linkedin/resolve",
)
async def linkedin_publication_resolve(
    calendar_event_id: str,
    request: LinkedInPublicationResolution,
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        return await resolve_linkedin_publication_uncertainty(
            settings,
            store,
            calendar_event_id,
            request.outcome,
            post_url=request.post_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        "growth_autopsy": "Growth Intelligence Report",
        "linkedin_post": "LinkedIn Growth Autopsy post",
        "strategy_doc": "One-problem Strategy Doc",
        "pitch_deck_brief": "Pitch Deck",
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


class AdminConfigUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    clear_secrets: list[str] = Field(default_factory=list)


class GoogleOAuthClientUpload(BaseModel):
    filename: str = Field(default="google-oauth-client.json", max_length=255)
    document: dict[str, Any]


@app.get("/internal/admin/database")
async def admin_database_overview(
    _: None = Depends(admin_service.require_local_admin),
    store: WorkflowStore = Depends(get_store),
) -> dict[str, Any]:
    return await asyncio.to_thread(admin_service.database_overview, store)


@app.get("/internal/admin/database/{table}/records/{rowid}")
async def admin_database_record(
    table: str,
    rowid: int,
    _: None = Depends(admin_service.require_local_admin),
    store: WorkflowStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            admin_service.database_record,
            store,
            table,
            rowid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/internal/admin/database/{table}")
async def admin_database_rows(
    table: str,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="", max_length=200),
    _: None = Depends(admin_service.require_local_admin),
    store: WorkflowStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            admin_service.database_rows,
            store,
            table,
            limit=limit,
            offset=offset,
            search=search,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/internal/admin/config")
async def admin_config(
    _: None = Depends(admin_service.require_local_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await asyncio.to_thread(admin_service.config_overview, settings)


@app.put("/internal/admin/config")
async def admin_config_update(
    request: AdminConfigUpdate,
    _: None = Depends(admin_service.require_local_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if settings.managed_configuration:
        raise HTTPException(
            status_code=409,
            detail=(
                "Production configuration is managed by the hosting provider. "
                "Update the service environment and redeploy."
            ),
        )
    try:
        updated = await asyncio.to_thread(
            admin_service.update_env_file,
            request.values,
            clear_secrets=request.clear_secrets,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "saved",
        "updated": updated,
        "restart_required": bool(updated),
    }


@app.post("/internal/admin/google-oauth-client")
async def admin_google_oauth_client(
    upload: GoogleOAuthClientUpload,
    _: None = Depends(admin_service.require_local_admin),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if settings.managed_configuration:
        raise HTTPException(
            status_code=409,
            detail=(
                "Upload OAuth files through the hosting provider's secret-file "
                "controls, not through the production application."
            ),
        )
    if not upload.filename.casefold().endswith(".json"):
        raise HTTPException(status_code=422, detail="Upload a JSON file")
    try:
        path = await asyncio.to_thread(
            admin_service.save_google_oauth_client,
            upload.document,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "uploaded",
        "client_file": str(path),
        "authorization_command": (
            "uv run growth-autopsy calendar-auth "
            "--client-secret ./secrets/google-oauth-client.json"
        ),
    }


def _linkedin_client(settings: Settings) -> LinkedInClient:
    return LinkedInClient(
        settings.linkedin_client_id,
        settings.linkedin_client_secret,
        settings.linkedin_redirect_uri,
        api_version=settings.linkedin_api_version,
    )


@app.get("/internal/linkedin/oauth/start")
async def linkedin_oauth_start(
    _: None = Depends(admin_service.require_local_admin),
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> RedirectResponse:
    if not settings.linkedin_enabled:
        raise HTTPException(
            status_code=409,
            detail="LinkedIn workflow is temporarily disabled",
        )
    client = _linkedin_client(settings)
    if not client.configured:
        raise HTTPException(
            status_code=409,
            detail="Save the LinkedIn client ID, client secret and redirect URI, then restart the app",
        )
    state_value = secrets.token_urlsafe(32)
    store.set_setting(
        "linkedin_oauth_state_sha256",
        hashlib.sha256(state_value.encode("utf-8")).hexdigest(),
    )
    store.set_setting("linkedin_oauth_state_created_at", datetime.now(UTC).isoformat())
    return RedirectResponse(client.authorization_url(state_value), status_code=302)


@app.get("/internal/linkedin/oauth/callback")
async def linkedin_oauth_callback(
    state: str = Query(default="", max_length=500),
    code: str = Query(default="", max_length=2_000),
    error: str = Query(default="", max_length=200),
    error_description: str = Query(default="", max_length=1_000),
    _: None = Depends(admin_service.require_local_admin),
    settings: Settings = Depends(get_settings),
    store: WorkflowStore = Depends(get_store),
) -> Response:
    expected_hash = store.get_setting("linkedin_oauth_state_sha256") or ""
    created_raw = store.get_setting("linkedin_oauth_state_created_at") or ""
    store.set_setting("linkedin_oauth_state_sha256", "")
    store.set_setting("linkedin_oauth_state_created_at", "")
    try:
        created_at = datetime.fromisoformat(created_raw).astimezone(UTC)
    except ValueError:
        created_at = datetime.min.replace(tzinfo=UTC)
    supplied_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    state_age = datetime.now(UTC) - created_at
    state_valid = bool(
        state
        and expected_hash
        and hmac.compare_digest(supplied_hash, expected_hash)
        and timedelta(0) <= state_age <= timedelta(minutes=10)
    )
    if not state_valid:
        raise HTTPException(status_code=400, detail="LinkedIn OAuth state is invalid or expired")
    if error:
        message = error_description or error
        return HTMLResponse(
            "<h1>LinkedIn connection was not completed</h1>"
            f"<p>{escape(message)}</p><p><a href='/admin/#configuration'>Return to Admin</a></p>",
            status_code=400,
        )
    if not code:
        raise HTTPException(status_code=400, detail="LinkedIn did not return an authorization code")
    try:
        token_payload, member_sub = await _linkedin_client(settings).exchange_code(code)
        await asyncio.to_thread(
            LinkedInTokenStore(settings.linkedin_token_file).save,
            token_payload,
            member_sub,
        )
    except LinkedInAuthorizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse("/admin/?linkedin=connected#configuration", status_code=303)


STATIC_DIR = Path(__file__).with_name("dashboard")
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
