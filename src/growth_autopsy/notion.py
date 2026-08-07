from __future__ import annotations

from typing import Any

import httpx


class NotionError(RuntimeError):
    pass


class NotionClient:
    """Create private child pages with Notion's enhanced Markdown API."""

    def __init__(
        self,
        api_key: str,
        parent_page_id: str,
        *,
        api_version: str = "2026-03-11",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.parent_page_id = parent_page_id
        self.api_version = api_version
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.parent_page_id)

    async def create_markdown_page(self, markdown: str) -> dict[str, Any]:
        if not self.configured:
            raise NotionError("Notion API key and parent page ID are required")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": self.api_version,
        }
        async with httpx.AsyncClient(
            base_url="https://api.notion.com",
            headers=headers,
            timeout=30,
            transport=self.transport,
            trust_env=False,
        ) as client:
            response = await client.post(
                "/v1/pages",
                json={
                    "parent": {"page_id": self.parent_page_id},
                    "markdown": markdown,
                },
            )
        if response.is_error:
            raise NotionError(
                f"Notion page creation failed ({response.status_code}): {response.text[:500]}"
            )
        payload = response.json()
        if not payload.get("id"):
            raise NotionError("Notion created a page without returning an id")
        return payload
