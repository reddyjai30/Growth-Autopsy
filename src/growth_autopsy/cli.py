from __future__ import annotations

import argparse
import asyncio
import json

from .config import get_settings
from .controller import collect_agent_outputs, run_due_precall_research, sync_calendar_once
from .hermes import HermesClient
from .store import WorkflowStore


def _build_runtime():
    settings = get_settings()
    store = WorkflowStore(settings.database_path)
    store.initialize()
    hermes = HermesClient(
        settings.hermes_base_url,
        settings.hermes_api_key,
        delivery_target=settings.hermes_delivery_target,
    )
    return settings, store, hermes


async def _calendar_sync() -> dict:
    settings, store, hermes = _build_runtime()
    result = await sync_calendar_once(settings, store)
    result["research"] = await run_due_precall_research(settings, store, hermes)
    result["agent_outputs"] = await collect_agent_outputs(settings, store, hermes)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="growth-autopsy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Initialize the local workflow database")
    subparsers.add_parser("calendar-sync", help="Ingest calendar changes once")
    status_parser = subparsers.add_parser("status", help="List recent appointments")
    status_parser.add_argument("--limit", type=int, default=20)
    serve_parser = subparsers.add_parser("serve", help="Run the Fathom webhook service")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if args.command == "init-db":
        settings = get_settings()
        WorkflowStore(settings.database_path).initialize()
        print(json.dumps({"status": "initialized", "database": str(settings.database_path)}))
        return

    if args.command == "calendar-sync":
        print(json.dumps(asyncio.run(_calendar_sync()), indent=2))
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
