from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .ai import AIClient
from .calendar_auth import CALENDAR_READONLY_SCOPE, authorize_google_calendar
from .calendar_ingestion import (
    GoogleCalendarGateway,
    calendar_conference_url,
    parse_calendar_event,
)
from .config import get_settings
from .controller import run_due_precall_research, sync_calendar_once
from .semrush_mcp import SemrushMCPClient
from .store import WorkflowStore


def _build_runtime():
    settings = get_settings()
    store = WorkflowStore(settings.database_path)
    store.initialize()
    ai = AIClient(
        settings.ai_base_url,
        settings.ai_api_key,
        settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
    )
    return settings, store, ai


async def _calendar_sync() -> dict:
    settings, store, ai = _build_runtime()
    result = await sync_calendar_once(settings, store)
    result["research"] = await run_due_precall_research(settings, store, ai)
    return result


async def _calendar_check() -> dict:
    settings = get_settings()
    gateway = GoogleCalendarGateway(
        settings.google_token_file,
        settings.google_calendar_id,
        lookback_hours=settings.calendar_lookback_hours,
        lookahead_days=settings.calendar_lookahead_days,
    )
    events = await asyncio.to_thread(gateway.list_events)
    matches = []
    for event in events:
        appointment = parse_calendar_event(
            event,
            calendar_id=settings.google_calendar_id,
            title_prefix=settings.calendar_title_prefix,
            diksha_email=settings.diksha_email,
        )
        if appointment is None:
            continue
        matches.append(
            {
                "event_id": appointment.calendar_event_id,
                "title": appointment.title,
                "company": appointment.company,
                "website": appointment.website,
                "start_at": appointment.start_at.isoformat(),
                "status": appointment.status.value,
                "conference_url": calendar_conference_url(event),
            }
        )
    return {
        "status": "connected",
        "calendar_id": settings.google_calendar_id,
        "events_scanned": len(events),
        "matching_events": matches,
        "workflow_state_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="growth-autopsy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Initialize the local workflow database")
    auth_parser = subparsers.add_parser(
        "calendar-auth",
        help="Authorize read-only Google Calendar access",
    )
    auth_parser.add_argument(
        "--client-secret",
        type=Path,
        required=True,
        help="Path to the downloaded Google OAuth Desktop app JSON",
    )
    auth_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing authorized-user token",
    )
    subparsers.add_parser(
        "calendar-check",
        help="Verify Calendar access and preview matching events without changing SQL state",
    )
    subparsers.add_parser("calendar-sync", help="Ingest calendar changes once")
    subparsers.add_parser(
        "semrush-check",
        help="Verify Semrush MCP authentication and list tools without executing reports",
    )
    status_parser = subparsers.add_parser("status", help="List recent appointments")
    status_parser.add_argument("--limit", type=int, default=20)
    serve_parser = subparsers.add_parser(
        "serve", help="Run the local dashboard and automation service"
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if args.command == "init-db":
        settings = get_settings()
        WorkflowStore(settings.database_path).initialize()
        print(json.dumps({"status": "initialized", "database": str(settings.database_path)}))
        return

    if args.command == "calendar-auth":
        settings = get_settings()
        token_file = authorize_google_calendar(
            args.client_secret,
            settings.google_token_file,
            force=args.force,
        )
        print(
            json.dumps(
                {
                    "status": "authorized",
                    "token_file": str(token_file),
                    "scope": CALENDAR_READONLY_SCOPE,
                }
            )
        )
        return

    if args.command == "calendar-sync":
        print(json.dumps(asyncio.run(_calendar_sync()), indent=2))
        return

    if args.command == "calendar-check":
        print(json.dumps(asyncio.run(_calendar_check()), indent=2))
        return

    if args.command == "semrush-check":
        settings = get_settings()
        print(
            json.dumps(
                asyncio.run(SemrushMCPClient(settings).check()),
                indent=2,
            )
        )
        return

    if args.command == "status":
        _, store, _ = _build_runtime()
        rows = [
            {
                "event_id": item.calendar_event_id,
                "company": item.company,
                "start_at": item.start_at.isoformat(),
                "status": item.status.value,
                "research_job_id": item.research_job_id,
                "error": item.last_error,
            }
            for item in store.list_appointments(limit=args.limit)
        ]
        print(json.dumps(rows, indent=2))
        return

    if args.command == "serve":
        import uvicorn

        uvicorn.run("growth_autopsy.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
