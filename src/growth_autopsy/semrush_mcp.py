from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .research import normalize_website_url


DISCOVERY_ORDER = ("domain_overview", "organic_research", "traffic_overview")
REQUIRED_TOOLS = {*DISCOVERY_ORDER, "get_report_schema", "execute_report"}
REPORT_HINTS = {
    "domain_overview": ("domain", "overview", "rank"),
    "organic_research": ("organic", "keyword", "position"),
    "traffic_overview": ("traffic", "summary", "overview"),
}


class SemrushMCPError(RuntimeError):
    pass


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _plain(model_dump(mode="json", exclude_none=True))
    text = getattr(value, "text", None)
    if text is not None:
        return str(text)
    return str(value)


def _tool_result(result: Any) -> dict[str, Any]:
    value = _plain(result)
    if isinstance(value, dict):
        if value.get("isError") or value.get("is_error"):
            detail = json.dumps(value.get("content") or value, ensure_ascii=False)
            raise SemrushMCPError(f"Semrush MCP tool returned an error: {detail[:500]}")
        return value
    return {"content": value}


def _iter_values(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                yield from _iter_values(json.loads(stripped))
            except ValueError:
                pass


def _report_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    report_keys = {"report", "report_id", "report_name", "id", "name", "type"}
    for item in _iter_values(value):
        if isinstance(item, dict):
            for key, raw in item.items():
                if str(key).casefold() in report_keys and isinstance(raw, str):
                    candidates.append(raw.strip())
        elif isinstance(item, str):
            candidates.extend(
                re.findall(
                    r"(?i)(?:report(?:_id|_name)?)[\s:=`\"']+([a-z0-9_.:/-]{3,100})",
                    item,
                )
            )
    return list(dict.fromkeys(item for item in candidates if item))


def _choose_report(category: str, discovery: Any) -> str:
    hints = REPORT_HINTS[category]
    candidates = _report_candidates(discovery)
    if not candidates:
        return ""
    return max(
        candidates,
        key=lambda item: sum(hint in item.casefold() for hint in hints),
    )


def _find_json_schema(value: Any) -> dict[str, Any]:
    for item in _iter_values(value):
        if isinstance(item, dict) and isinstance(item.get("properties"), dict):
            return item
    return {}


def _schema_for_tool(tool: Any) -> dict[str, Any]:
    raw = _plain(tool)
    if not isinstance(raw, dict):
        return {}
    schema = raw.get("inputSchema") or raw.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def _tool_name(tool: Any) -> str:
    raw = _plain(tool)
    return str(raw.get("name") or "") if isinstance(raw, dict) else ""


def _value_for_property(
    name: str,
    schema: dict[str, Any],
    *,
    domain: str,
    database: str,
    country: str,
    report: str,
    report_parameters: dict[str, Any] | None,
) -> Any:
    lowered = name.casefold()
    value: Any = None
    if "report" in lowered and report:
        value = report
    elif lowered in {"parameters", "params", "arguments", "report_parameters"}:
        value = report_parameters or {}
    elif lowered in {"domain", "target", "url", "website"}:
        value = domain
    elif lowered in {"targets", "domains"}:
        value = [domain]
    elif "database" in lowered:
        value = database
    elif lowered in {"country", "country_code", "regional_database"}:
        value = country
    elif lowered in {"limit", "display_limit", "row_limit", "results"}:
        value = 10
    elif "offset" in lowered:
        value = 0
    elif "export" in lowered and "column" in lowered:
        value = ""
    elif "date" in lowered:
        value = None
    elif "default" in schema:
        value = schema["default"]

    expected = schema.get("type")
    if expected == "array" and value is not None and not isinstance(value, list):
        value = [value]
    return value


def _build_arguments(
    schema: dict[str, Any],
    *,
    domain: str,
    database: str,
    country: str,
    report: str = "",
    report_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    arguments: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            continue
        value = _value_for_property(
            str(name),
            definition,
            domain=domain,
            database=database,
            country=country,
            report=report,
            report_parameters=report_parameters,
        )
        if value is not None and (name in required or value != ""):
            arguments[str(name)] = value
    unknown = [name for name in required if name not in arguments]
    if unknown:
        raise SemrushMCPError(
            f"Unsupported required Semrush MCP parameter(s): {', '.join(sorted(unknown))}"
        )
    return arguments


class SemrushMCPClient:
    """Bounded read-only client for the official Semrush Streamable HTTP MCP."""

    def __init__(
        self,
        settings: Settings,
        *,
        connector: Callable[[], Any] | None = None,
    ):
        self.enabled = settings.semrush_mcp_enabled
        self.url = settings.semrush_mcp_url
        self.api_key = settings.semrush_api_key
        self.database = settings.semrush_database
        self.country = settings.semrush_country
        self.timeout = settings.semrush_timeout_seconds
        self.max_reports = settings.semrush_mcp_max_reports
        self.connector = connector

    def _validate_configuration(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.hostname != "mcp.semrush.com":
            raise SemrushMCPError(
                "GA_SEMRUSH_MCP_URL must use the official https://mcp.semrush.com host"
            )
        if not self.api_key:
            raise SemrushMCPError(
                "GA_SEMRUSH_API_KEY is required for direct Semrush MCP authentication"
            )

    @asynccontextmanager
    async def _connect(self):
        if self.connector is not None:
            async with self.connector() as session:
                yield session
            return
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        original = httpx.URL(self.url)

        async def strip_cross_origin_auth(response: httpx.Response) -> None:
            next_request = response.next_request
            if not response.is_redirect or next_request is None:
                return
            target = next_request.url
            if (target.scheme, target.host, target.port) != (
                original.scheme,
                original.host,
                original.port,
            ):
                next_request.headers.pop("authorization", None)

        timeout = httpx.Timeout(float(self.timeout), read=float(self.timeout))
        async with httpx.AsyncClient(
            headers={"Authorization": f"Apikey {self.api_key}"},
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            event_hooks={"response": [strip_cross_origin_auth]},
        ) as http_client:
            async with streamable_http_client(
                self.url, http_client=http_client
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session

    async def check(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "endpoint": self.url}
        self._validate_configuration()
        async with self._connect() as session:
            listed = await session.list_tools()
        tools = list(getattr(listed, "tools", listed if isinstance(listed, list) else []))
        names = sorted(_tool_name(tool) for tool in tools if _tool_name(tool))
        return {
            "status": "connected",
            "endpoint": self.url,
            "tools": names,
            "required_tools_available": sorted(REQUIRED_TOOLS.intersection(names)),
            "missing_required_tools": sorted(REQUIRED_TOOLS.difference(names)),
        }

    async def collect(self, website: str) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "provider": "Semrush MCP",
                "caveat": "Public-data collectors continued without paid Semrush enrichment.",
            }
        try:
            self._validate_configuration()
            domain = (urlsplit(normalize_website_url(website)).hostname or "").removeprefix(
                "www."
            )
            async with self._connect() as session:
                listed = await session.list_tools()
                tool_list = list(
                    getattr(listed, "tools", listed if isinstance(listed, list) else [])
                )
                tools = {_tool_name(tool): tool for tool in tool_list if _tool_name(tool)}
                missing = REQUIRED_TOOLS.difference(tools)
                if missing:
                    raise SemrushMCPError(
                        f"Semrush MCP is missing required tools: {', '.join(sorted(missing))}"
                    )

                reports: list[dict[str, Any]] = []
                for category in DISCOVERY_ORDER[: self.max_reports]:
                    try:
                        discovery_args = _build_arguments(
                            _schema_for_tool(tools[category]),
                            domain=domain,
                            database=self.database,
                            country=self.country,
                        )
                        discovery_raw = await session.call_tool(
                            category, arguments=discovery_args
                        )
                        discovery = _tool_result(discovery_raw)
                        report = _choose_report(category, discovery)
                        if not report:
                            reports.append(
                                {
                                    "category": category,
                                    "status": "discovery_only",
                                    "discovery": discovery,
                                    "caveat": "No report identifier could be selected safely.",
                                }
                            )
                            continue
                        schema_args = _build_arguments(
                            _schema_for_tool(tools["get_report_schema"]),
                            domain=domain,
                            database=self.database,
                            country=self.country,
                            report=report,
                        )
                        schema_raw = await session.call_tool(
                            "get_report_schema", arguments=schema_args
                        )
                        report_schema_result = _tool_result(schema_raw)
                        report_schema = _find_json_schema(report_schema_result)
                        report_parameters = _build_arguments(
                            report_schema,
                            domain=domain,
                            database=self.database,
                            country=self.country,
                            report=report,
                        )
                        execute_args = _build_arguments(
                            _schema_for_tool(tools["execute_report"]),
                            domain=domain,
                            database=self.database,
                            country=self.country,
                            report=report,
                            report_parameters=report_parameters,
                        )
                        result_raw = await session.call_tool(
                            "execute_report", arguments=execute_args
                        )
                        result = _tool_result(result_raw)
                        encoded = json.dumps(result, ensure_ascii=False)
                        if len(encoded.encode("utf-8")) > 200_000:
                            raise SemrushMCPError(
                                "Semrush report exceeded the 200,000-byte evidence limit"
                            )
                        reports.append(
                            {
                                "category": category,
                                "report": report,
                                "status": "available",
                                "arguments": execute_args,
                                "result": result,
                            }
                        )
                    except Exception as exc:
                        reports.append(
                            {
                                "category": category,
                                "status": "unavailable",
                                "error": str(exc)[:500],
                            }
                        )
            available = any(item.get("status") == "available" for item in reports)
            return {
                "status": "available" if available else "unavailable",
                "provider": "Semrush official MCP",
                "endpoint": self.url,
                "domain": domain,
                "reports": reports,
                "executed_report_limit": self.max_reports,
                "caveat": (
                    "Semrush values are third-party estimates. MCP calls consume subscription "
                    "units and do not expose private analytics, ROAS, CPA, or conversion data."
                ),
            }
        except Exception as exc:
            return {
                "status": "unavailable",
                "provider": "Semrush official MCP",
                "endpoint": self.url,
                "error": str(exc)[:500],
                "caveat": "The public-data pipeline continued without Semrush.",
            }
