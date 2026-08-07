from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from growth_autopsy.config import Settings
from growth_autopsy.semrush_mcp import SemrushMCPClient


def tool(name: str, properties: dict, required: list[str] | None = None):
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


class FakeSession:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.tools = [
            tool(name, {})
            for name in ("domain_overview", "organic_research", "traffic_overview")
        ] + [
            tool("get_report_schema", {"report": {"type": "string"}}, ["report"]),
            tool(
                "execute_report",
                {
                    "report": {"type": "string"},
                    "parameters": {"type": "object"},
                },
                ["report", "parameters"],
            ),
        ]

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict):
        if name in {"domain_overview", "organic_research", "traffic_overview"}:
            return {"content": [{"text": f'{{"reports":[{{"report_id":"{name}_report"}}]}}'}]}
        if name == "get_report_schema":
            return {
                "content": [
                    {
                        "text": (
                            '{"type":"object","properties":{"domain":{"type":"string"},'
                            '"database":{"type":"string"},"limit":{"type":"integer"}},'
                            '"required":["domain","database"]}'
                        )
                    }
                ]
            }
        self.execute_calls += 1
        return {"structuredContent": {"rows": [{"domain": "example.com", "value": 1}]}}


@pytest.mark.asyncio
async def test_semrush_mcp_is_optional() -> None:
    result = await SemrushMCPClient(
        Settings(semrush_mcp_enabled=False, semrush_api_key="")
    ).collect("https://example.com")
    assert result["status"] == "disabled"


@pytest.mark.asyncio
async def test_semrush_mcp_executes_no_more_than_configured_reports() -> None:
    session = FakeSession()

    @asynccontextmanager
    async def connector():
        yield session

    settings = Settings(
        semrush_mcp_enabled=True,
        semrush_api_key="licensed-key",
        semrush_mcp_max_reports=2,
    )
    result = await SemrushMCPClient(settings, connector=connector).collect(
        "https://example.com"
    )

    assert result["status"] == "available"
    assert session.execute_calls == 2
    assert len(result["reports"]) == 2
    assert result["reports"][0]["arguments"]["parameters"]["domain"] == "example.com"


@pytest.mark.asyncio
async def test_semrush_mcp_check_does_not_execute_reports() -> None:
    session = FakeSession()

    @asynccontextmanager
    async def connector():
        yield session

    settings = Settings(semrush_mcp_enabled=True, semrush_api_key="licensed-key")
    result = await SemrushMCPClient(settings, connector=connector).check()

    assert result["status"] == "connected"
    assert session.execute_calls == 0
    assert result["missing_required_tools"] == []
