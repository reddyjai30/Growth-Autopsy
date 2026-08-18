from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values
from fastapi import HTTPException, Request

from .config import Settings
from .store import WorkflowStore


ADMIN_TABLES: dict[str, dict[str, str]] = {
    "appointments": {
        "label": "Appointments",
        "description": "Calendar bookings and current workflow state",
    },
    "recordings": {
        "label": "Recordings",
        "description": "Matched Fathom recordings and transcript metadata",
    },
    "artifacts": {
        "label": "Artifacts",
        "description": "Research, intelligence, strategy and publishing outputs",
    },
    "workflow_events": {
        "label": "Workflow events",
        "description": "Chronological automation activity and errors",
    },
    "webhook_deliveries": {
        "label": "Webhook deliveries",
        "description": "Fathom delivery attempts and replay protection",
    },
    "settings": {
        "label": "Runtime settings",
        "description": "Non-secret workflow decisions and controller metadata",
    },
    "dismissed_appointments": {
        "label": "Dismissed meetings",
        "description": "Calendar events intentionally removed from the dashboard",
    },
}

MAX_DATABASE_PAGE_SIZE = 100
MAX_LIST_CELL_CHARACTERS = 3_000
MAX_RECORD_CELL_CHARACTERS = 1_000_000
MAX_OAUTH_DOCUMENT_BYTES = 100_000


def require_local_admin(request: Request) -> None:
    """Allow loopback development or an authenticated production operator."""

    if getattr(request.state, "operator_authenticated", False) is True:
        return

    def is_loopback(host: str) -> bool:
        if host.strip().casefold() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host.strip()).is_loopback
        except ValueError:
            return False

    client_host = request.client.host if request.client else ""
    request_host = request.url.hostname or ""
    if is_loopback(client_host) and is_loopback(request_host):
        return
    raise HTTPException(
        status_code=403,
        detail="The admin console requires local or authenticated operator access",
    )


def env_file_path() -> Path:
    return (Path.cwd() / ".env").resolve()


def oauth_client_file_path() -> Path:
    return (env_file_path().parent / "secrets" / "google-oauth-client.json").resolve()


def _read_only_connection(store: WorkflowStore) -> sqlite3.Connection:
    uri = store.path.expanduser().resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_columns(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table)})"
    ).fetchall()
    return [
        {
            "name": str(row["name"]),
            "type": str(row["type"] or "TEXT"),
            "not_null": bool(row["notnull"]),
            "primary_key": bool(row["pk"]),
        }
        for row in rows
    ]


def _database_value(value: Any, *, limit: int) -> Any:
    if isinstance(value, bytes):
        rendered = value.hex()
    elif isinstance(value, (str, int, float)) or value is None:
        rendered = value
    else:
        rendered = str(value)
    if isinstance(rendered, str) and len(rendered) > limit:
        return {
            "preview": rendered[:limit],
            "truncated": True,
            "characters": len(rendered),
        }
    return rendered


def database_overview(store: WorkflowStore) -> dict[str, Any]:
    with _read_only_connection(store) as connection:
        tables = []
        for key, metadata in ADMIN_TABLES.items():
            count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(key)}"
                ).fetchone()[0]
            )
            tables.append(
                {
                    "key": key,
                    "label": metadata["label"],
                    "description": metadata["description"],
                    "count": count,
                    "columns": _table_columns(connection, key),
                }
            )
    return {
        "engine": "SQLite",
        "database_file": str(store.path),
        "read_only": True,
        "tables": tables,
    }


def _search_clause(columns: list[dict[str, Any]], search: str) -> tuple[str, list[str]]:
    query = search.strip()
    if not query:
        return "", []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    term = f"%{escaped}%"
    predicates = [
        f"CAST({_quote_identifier(column['name'])} AS TEXT) LIKE ? ESCAPE '\\'"
        for column in columns
    ]
    return " WHERE " + " OR ".join(predicates), [term] * len(predicates)


def database_rows(
    store: WorkflowStore,
    table: str,
    *,
    limit: int = 25,
    offset: int = 0,
    search: str = "",
) -> dict[str, Any]:
    if table not in ADMIN_TABLES:
        raise ValueError("Unknown admin database table")
    safe_limit = max(1, min(int(limit), MAX_DATABASE_PAGE_SIZE))
    safe_offset = max(0, int(offset))
    with _read_only_connection(store) as connection:
        columns = _table_columns(connection, table)
        where, parameters = _search_clause(columns, search)
        quoted_table = _quote_identifier(table)
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table}{where}", parameters
            ).fetchone()[0]
        )
        column_names = {column["name"] for column in columns}
        if "updated_at" in column_names:
            order = '"updated_at" DESC'
        elif "created_at" in column_names:
            order = '"created_at" DESC'
        elif any(column["primary_key"] for column in columns):
            primary = next(column["name"] for column in columns if column["primary_key"])
            order = f"{_quote_identifier(primary)} DESC"
        else:
            order = "rowid DESC"
        rows = connection.execute(
            f"SELECT rowid AS __rowid__, * FROM {quoted_table}{where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*parameters, safe_limit, safe_offset],
        ).fetchall()
    return {
        "table": table,
        "label": ADMIN_TABLES[table]["label"],
        "columns": columns,
        "rows": [
            {
                key: _database_value(row[key], limit=MAX_LIST_CELL_CHARACTERS)
                for key in row.keys()
            }
            for row in rows
        ],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "search": search.strip(),
    }


def database_record(store: WorkflowStore, table: str, rowid: int) -> dict[str, Any]:
    if table not in ADMIN_TABLES:
        raise ValueError("Unknown admin database table")
    with _read_only_connection(store) as connection:
        row = connection.execute(
            f"SELECT rowid AS __rowid__, * FROM {_quote_identifier(table)} "
            "WHERE rowid=?",
            (int(rowid),),
        ).fetchone()
    if row is None:
        raise LookupError("Database record not found")
    return {
        "table": table,
        "record": {
            key: _database_value(row[key], limit=MAX_RECORD_CELL_CHARACTERS)
            for key in row.keys()
        },
    }


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    setting: str
    label: str
    group: str
    kind: str = "text"
    secret: bool = False
    help: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


CONFIG_FIELDS = (
    ConfigField("GA_ENVIRONMENT", "environment", "Environment", "General", choices=("development", "production")),
    ConfigField("GA_DATABASE_PATH", "database_path", "Database path", "General", kind="path", help="SQLite workflow database."),
    ConfigField("GA_SHARED_WORKDIR", "shared_workdir", "Shared data directory", "General", kind="path", help="Transcripts, artifacts and exports."),
    ConfigField("GA_APP_USERNAME", "app_username", "Operator username", "Security", help="Required for production dashboard access."),
    ConfigField("GA_APP_PASSWORD", "app_password", "Operator password", "Security", kind="password", secret=True, help="Use at least 12 characters in production."),
    ConfigField("GA_SESSION_SECRET", "session_secret", "Session signing secret", "Security", kind="password", secret=True, help="Use a random value of at least 32 characters."),
    ConfigField("GA_SESSION_TTL_HOURS", "session_ttl_hours", "Session lifetime", "Security", kind="number", help="Hours before an operator must sign in again.", minimum=1, maximum=168),
    ConfigField("GA_MANAGED_CONFIGURATION", "managed_configuration", "Provider-managed configuration", "Security", kind="boolean", help="Disable Admin writes only when an external platform owns runtime configuration."),
    ConfigField("GA_GOOGLE_CALENDAR_ID", "google_calendar_id", "Calendar ID", "Google Calendar", help="Use primary for the signed-in account."),
    ConfigField("GA_GOOGLE_TOKEN_FILE", "google_token_file", "Authorized token path", "Google Calendar", kind="path"),
    ConfigField("GA_CALENDAR_TITLE_PREFIX", "calendar_title_prefix", "Meeting title prefix", "Google Calendar"),
    ConfigField("GA_DIKSHA_EMAIL", "diksha_email", "Operator email", "Google Calendar", kind="email", help="Excluded when selecting the external attendee."),
    ConfigField("GA_CALENDAR_LOOKAHEAD_DAYS", "calendar_lookahead_days", "Look-ahead days", "Google Calendar", kind="number", minimum=1, maximum=365),
    ConfigField("GA_CALENDAR_LOOKBACK_HOURS", "calendar_lookback_hours", "Look-back hours", "Google Calendar", kind="number", minimum=0, maximum=168),
    ConfigField("GA_PRECALL_START_MINUTES", "precall_start_minutes", "Research starts before call", "Research", kind="number", help="Minutes before the meeting.", minimum=30, maximum=240),
    ConfigField("GA_PRECALL_DELIVERY_MINUTES", "precall_delivery_minutes", "Report target before call", "Research", kind="number", help="Minutes before the meeting.", minimum=5, maximum=120),
    ConfigField("GA_PRECALL_RESEARCH_BACKEND", "precall_research_backend", "Research backend", "Research", choices=("local_free",)),
    ConfigField("GA_PRECALL_MAX_PAGES", "precall_max_pages", "Maximum pages", "Research", kind="number", minimum=1, maximum=30),
    ConfigField("GA_PRECALL_MAX_CONCURRENCY", "precall_max_concurrency", "Crawl concurrency", "Research", kind="number", minimum=1, maximum=10),
    ConfigField("GA_PRECALL_HTTP_TIMEOUT_SECONDS", "precall_http_timeout_seconds", "HTTP timeout", "Research", kind="number", help="Seconds per website request.", minimum=5, maximum=90),
    ConfigField("GA_PRECALL_MAX_RESPONSE_BYTES", "precall_max_response_bytes", "Maximum response bytes", "Research", kind="number", minimum=65536, maximum=10485760),
    ConfigField("GA_PRECALL_SEARCH_ENABLED", "precall_search_enabled", "Public search", "Research", kind="boolean"),
    ConfigField("GA_PRECALL_SEARCH_RESULTS_PER_QUERY", "precall_search_results_per_query", "Search results per query", "Research", kind="number", minimum=1, maximum=10),
    ConfigField("GA_PRECALL_SEARCH_TIMEOUT_SECONDS", "precall_search_timeout_seconds", "Search timeout", "Research", kind="number", minimum=5, maximum=60),
    ConfigField("GA_PRECALL_PAGESPEED_ENABLED", "precall_pagespeed_enabled", "PageSpeed API", "Research", kind="boolean"),
    ConfigField("GA_PAGESPEED_API_KEY", "pagespeed_api_key", "PageSpeed API key", "Research", kind="password", secret=True, help="Optional; local Lighthouse remains available."),
    ConfigField("GA_PRECALL_PAGESPEED_TIMEOUT_SECONDS", "precall_pagespeed_timeout_seconds", "PageSpeed timeout", "Research", kind="number", minimum=10, maximum=120),
    ConfigField("GA_PRECALL_LOCAL_LIGHTHOUSE_ENABLED", "precall_local_lighthouse_enabled", "Local Lighthouse fallback", "Research", kind="boolean"),
    ConfigField("GA_LIGHTHOUSE_EXECUTABLE", "lighthouse_executable", "Lighthouse executable", "Research", kind="path"),
    ConfigField("GA_PRECALL_LOCAL_LIGHTHOUSE_TIMEOUT_SECONDS", "precall_local_lighthouse_timeout_seconds", "Lighthouse timeout", "Research", kind="number", minimum=30, maximum=180),
    ConfigField("GA_PRECALL_COLLECTION_STALE_MINUTES", "precall_collection_stale_minutes", "Stale collection timeout", "Research", kind="number", help="Minutes before a stuck collection may be retried.", minimum=5, maximum=120),
    ConfigField("GA_PRECALL_MAX_PARALLEL_APPOINTMENTS", "precall_max_parallel_appointments", "Parallel appointments", "Research", kind="number", minimum=1, maximum=5),
    ConfigField("GA_PLAYWRIGHT_ENABLED", "playwright_enabled", "Playwright rendering", "Research", kind="boolean"),
    ConfigField("GA_PLAYWRIGHT_TIMEOUT_SECONDS", "playwright_timeout_seconds", "Playwright timeout", "Research", kind="number", minimum=5, maximum=90),
    ConfigField("GA_PLAYWRIGHT_SETTLE_MILLISECONDS", "playwright_settle_milliseconds", "Page settle time", "Research", kind="number", help="Milliseconds allowed for rendered pages to settle.", minimum=0, maximum=10000),
    ConfigField("GA_SEMRUSH_MCP_ENABLED", "semrush_mcp_enabled", "Semrush enrichment", "Semrush", kind="boolean"),
    ConfigField("GA_SEMRUSH_API_KEY", "semrush_api_key", "Semrush API key", "Semrush", kind="password", secret=True),
    ConfigField("GA_SEMRUSH_DATABASE", "semrush_database", "Semrush database", "Semrush"),
    ConfigField("GA_SEMRUSH_COUNTRY", "semrush_country", "Semrush country", "Semrush"),
    ConfigField("GA_SEMRUSH_TIMEOUT_SECONDS", "semrush_timeout_seconds", "Semrush timeout", "Semrush", kind="number", minimum=5, maximum=60),
    ConfigField("GA_SEMRUSH_MCP_URL", "semrush_mcp_url", "Semrush MCP URL", "Semrush", kind="url"),
    ConfigField("GA_SEMRUSH_MCP_MAX_REPORTS", "semrush_mcp_max_reports", "Maximum reports", "Semrush", kind="number", minimum=1, maximum=5),
    ConfigField("GA_AI_BASE_URL", "ai_base_url", "AI API base URL", "AI synthesis", kind="url"),
    ConfigField("GA_AI_API_KEY", "ai_api_key", "AI API key", "AI synthesis", kind="password", secret=True),
    ConfigField("GA_AI_MODEL", "ai_model", "AI model", "AI synthesis"),
    ConfigField("GA_AI_TIMEOUT_SECONDS", "ai_timeout_seconds", "Request timeout", "AI synthesis", kind="number", minimum=10, maximum=300),
    ConfigField("GA_AI_MAX_OUTPUT_TOKENS", "ai_max_output_tokens", "Maximum output tokens", "AI synthesis", kind="number", minimum=1000, maximum=20000),
    ConfigField("GA_FATHOM_WEBHOOK_SECRET", "fathom_webhook_secret", "Webhook signing secret", "Fathom", kind="password", secret=True),
    ConfigField("GA_FATHOM_API_KEY", "fathom_api_key", "Fathom API key", "Fathom", kind="password", secret=True),
    ConfigField("GA_FATHOM_MATCH_WINDOW_MINUTES", "fathom_match_window_minutes", "Matching window", "Fathom", kind="number", help="Minutes around the Calendar start time.", minimum=5, maximum=120),
    ConfigField("GA_MAX_FATHOM_WEBHOOK_BYTES", "max_fathom_webhook_bytes", "Maximum webhook bytes", "Fathom", kind="number", minimum=1024, maximum=26214400),
    ConfigField("GA_NOTION_API_KEY", "notion_api_key", "Notion integration secret", "Notion", kind="password", secret=True),
    ConfigField("GA_NOTION_PARENT_PAGE_ID", "notion_parent_page_id", "Parent page ID", "Notion", kind="password", secret=True),
    ConfigField("GA_NOTION_API_VERSION", "notion_api_version", "API version", "Notion"),
    ConfigField("GA_NOTION_PUBLISH_AFTER_APPROVAL", "notion_publish_after_approval", "Publish after final approval", "Notion", kind="boolean"),
    ConfigField("GA_LINKEDIN_CLIENT_ID", "linkedin_client_id", "Client ID", "LinkedIn", help="LinkedIn Developer app client ID."),
    ConfigField("GA_LINKEDIN_CLIENT_SECRET", "linkedin_client_secret", "Client secret", "LinkedIn", kind="password", secret=True),
    ConfigField("GA_LINKEDIN_REDIRECT_URI", "linkedin_redirect_uri", "OAuth redirect URI", "LinkedIn", kind="url", help="Must exactly match the URL registered in LinkedIn Developer Portal."),
    ConfigField("GA_LINKEDIN_TOKEN_FILE", "linkedin_token_file", "Authorized token path", "LinkedIn", kind="path"),
    ConfigField("GA_LINKEDIN_API_VERSION", "linkedin_api_version", "Posts API version", "LinkedIn", help="LinkedIn YYYYMM API version."),
    ConfigField("GA_LINKEDIN_PUBLISH_AFTER_NOTION", "linkedin_publish_after_notion", "Publish after Notion", "LinkedIn", kind="boolean", help="Publishes the approved post only after the Notion package succeeds."),
    ConfigField("GA_ENABLE_BACKGROUND_SYNC", "enable_background_sync", "Background automation", "Automation", kind="boolean"),
    ConfigField("GA_BACKGROUND_SYNC_INTERVAL_SECONDS", "background_sync_interval_seconds", "Sync interval", "Automation", kind="number", help="Seconds between controller ticks.", minimum=15, maximum=3600),
)

CONFIG_FIELD_BY_KEY = {field.key: field for field in CONFIG_FIELDS}


def _setting_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def config_overview(settings: Settings) -> dict[str, Any]:
    path = env_file_path()
    parsed = dotenv_values(path) if path.is_file() else {}
    fields = []
    for definition in CONFIG_FIELDS:
        raw = parsed.get(definition.key)
        process_value = os.environ.get(definition.key)
        effective = getattr(settings, definition.setting)
        configured = bool(
            str(raw if raw is not None else process_value or _setting_text(effective)).strip()
        )
        if definition.key in parsed:
            source = "env_file"
        elif definition.key in os.environ:
            source = "process_environment"
        else:
            source = "default"
        fields.append(
            {
                "key": definition.key,
                "label": definition.label,
                "group": definition.group,
                "kind": definition.kind,
                "secret": definition.secret,
                "help": definition.help,
                "choices": list(definition.choices),
                "minimum": definition.minimum,
                "maximum": definition.maximum,
                "configured": configured,
                "source": source,
                "value": "" if definition.secret else str(raw if raw is not None else _setting_text(effective)),
            }
        )
    managed = settings.managed_configuration
    return {
        "env_file": str(path),
        "env_exists": path.is_file(),
        "runtime_managed": managed,
        "configuration_note": (
            "Production values are managed by the hosting provider. Change them "
            "in the service environment and redeploy."
            if managed
            else "Local values are stored in the ignored .env file."
        ),
        "restart_required": False,
        "fields": fields,
        "google_oauth": {
            "client_file": str(oauth_client_file_path()),
            "client_uploaded": oauth_client_file_path().is_file(),
            "token_file": str(settings.google_token_file),
            "token_created": settings.google_token_file.is_file(),
            "authorization_command": (
                "uv run growth-autopsy calendar-auth "
                "--client-secret ./secrets/google-oauth-client.json"
            ),
        },
        "linkedin_oauth": _linkedin_oauth_overview(settings),
    }


def _linkedin_oauth_overview(settings: Settings) -> dict[str, Any]:
    # Import lazily so this read-only admin module never handles the token value.
    from .linkedin import LinkedInTokenStore

    token = LinkedInTokenStore(settings.linkedin_token_file).inspect()
    configured = bool(
        settings.linkedin_client_id
        and settings.linkedin_client_secret
        and settings.linkedin_redirect_uri
    )
    return {
        "configured": configured,
        "authorized": token["authorized"],
        "expired": token["expired"],
        "person_urn": token["person_urn"],
        "expires_at": token["expires_at"],
        "token_file": str(settings.linkedin_token_file),
        "connect_url": "/internal/linkedin/oauth/start",
        "publish_enabled": settings.linkedin_publish_after_notion,
    }


def _validate_config_value(field: ConfigField, raw: str) -> str:
    value = str(raw).strip()
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field.label} must be a single-line value")
    if len(value) > 10_000:
        raise ValueError(f"{field.label} is too long")
    if field.kind == "boolean":
        normalized = value.casefold()
        if normalized not in {"true", "false"}:
            raise ValueError(f"{field.label} must be true or false")
        return normalized
    if field.kind == "number":
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{field.label} must be a whole number") from exc
        if field.minimum is not None and number < field.minimum:
            raise ValueError(f"{field.label} must be at least {field.minimum}")
        if field.maximum is not None and number > field.maximum:
            raise ValueError(f"{field.label} must be at most {field.maximum}")
        return str(number)
    if field.choices and value not in field.choices:
        raise ValueError(f"{field.label} must be one of: {', '.join(field.choices)}")
    if field.key == "GA_LINKEDIN_API_VERSION" and not re.fullmatch(r"\d{6}", value):
        raise ValueError("LinkedIn Posts API version must use YYYYMM format")
    if field.kind == "url" and value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{field.label} must be a valid HTTP or HTTPS URL")
    if field.kind == "email" and value:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError(f"{field.label} must be a valid email address")
    return value


def _format_env_assignment(key: str, value: str) -> str:
    return f"{key}={json.dumps(value, ensure_ascii=False)}"


def update_env_file(
    values: dict[str, str],
    *,
    clear_secrets: list[str] | None = None,
) -> list[str]:
    clear = set(clear_secrets or [])
    unknown = (set(values) | clear).difference(CONFIG_FIELD_BY_KEY)
    if unknown:
        raise ValueError("Unsupported configuration key(s): " + ", ".join(sorted(unknown)))
    invalid_clear = [key for key in clear if not CONFIG_FIELD_BY_KEY[key].secret]
    if invalid_clear:
        raise ValueError("Only secret fields can be explicitly cleared")

    updates: dict[str, str] = {}
    for key, raw in values.items():
        field = CONFIG_FIELD_BY_KEY[key]
        if field.secret and not str(raw).strip() and key not in clear:
            continue
        updates[key] = _validate_config_value(field, "" if key in clear else raw)
    for key in clear:
        updates[key] = ""
    if not updates:
        return []

    path = env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    remaining = dict(updates)
    output: list[str] = []
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in original.splitlines():
        match = assignment.match(line)
        key = match.group(1) if match else ""
        if key in remaining:
            output.append(_format_env_assignment(key, remaining.pop(key)))
        else:
            output.append(line)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Updated from the local Growth Autopsy admin console")
        for key in CONFIG_FIELD_BY_KEY:
            if key in remaining:
                output.append(_format_env_assignment(key, remaining[key]))
    content = "\n".join(output).rstrip() + "\n"

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return list(updates)


def save_google_oauth_client(document: dict[str, Any]) -> Path:
    encoded = json.dumps(document, ensure_ascii=False, indent=2)
    if len(encoded.encode("utf-8")) > MAX_OAUTH_DOCUMENT_BYTES:
        raise ValueError("Google OAuth client JSON exceeds the 100 KB limit")
    installed = document.get("installed")
    if not isinstance(installed, dict):
        raise ValueError("Upload a Google OAuth Desktop app JSON containing 'installed'")
    required = ("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")
    missing = [key for key in required if not installed.get(key)]
    if missing:
        raise ValueError("Google OAuth JSON is missing: " + ", ".join(missing))
    if not isinstance(installed.get("redirect_uris"), list):
        raise ValueError("Google OAuth redirect_uris must be a list")
    if not str(installed["client_id"]).endswith(".apps.googleusercontent.com"):
        raise ValueError("Google OAuth client_id is not a Desktop OAuth client ID")
    for key in ("auth_uri", "token_uri"):
        parsed = urlsplit(str(installed[key]))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"Google OAuth {key} must be an HTTPS URL")

    path = oauth_client_file_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(encoded + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return path
