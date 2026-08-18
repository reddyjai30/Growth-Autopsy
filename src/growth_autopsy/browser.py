from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .research import FetchResult, ResearchError, assert_public_hostname, normalize_website_url


@dataclass(slots=True)
class BrowserRenderResult:
    fetch: FetchResult
    blocked_requests: int


class PlaywrightRenderer:
    """Render a public page in an isolated, disposable Chromium context."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        settle_milliseconds: int,
        max_response_bytes: int,
    ):
        self.timeout_ms = timeout_seconds * 1000
        self.settle_milliseconds = settle_milliseconds
        self.max_response_bytes = max_response_bytes

    async def render(self, raw_url: str) -> BrowserRenderResult:
        url = normalize_website_url(raw_url)
        await assert_public_hostname(urlsplit(url).hostname or "")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ResearchError("Playwright is not installed") from exc

        started = time.monotonic()
        blocked = 0
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise ResearchError(
                    "Playwright Chromium is unavailable; run `playwright install chromium`"
                ) from exc
            try:
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=True,
                    viewport={"width": 1440, "height": 1000},
                )

                async def guard(route) -> None:
                    nonlocal blocked
                    request = route.request
                    parsed = urlsplit(request.url)
                    if parsed.scheme not in {"http", "https"}:
                        blocked += 1
                        await route.abort()
                        return
                    try:
                        await assert_public_hostname(parsed.hostname or "")
                    except Exception:
                        blocked += 1
                        await route.abort()
                        return
                    if request.resource_type in {"media", "font"}:
                        blocked += 1
                        await route.abort()
                        return
                    await route.continue_()

                await context.route("**/*", guard)
                page = await context.new_page()
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                if self.settle_milliseconds:
                    await page.wait_for_timeout(self.settle_milliseconds)
                final_url = page.url
                await assert_public_hostname(urlsplit(final_url).hostname or "")
                html = await page.content()
                if len(html.encode("utf-8")) > self.max_response_bytes:
                    raise ResearchError("Rendered page exceeded the configured size limit")
                headers = await response.all_headers() if response else {}
                safe_headers = {
                    key.casefold(): str(value)[:1000]
                    for key, value in headers.items()
                    if key.casefold()
                    in {
                        "cache-control",
                        "content-language",
                        "content-type",
                        "server",
                        "strict-transport-security",
                        "x-frame-options",
                    }
                }
                fetch = FetchResult(
                    requested_url=url,
                    final_url=final_url,
                    status_code=response.status if response else 200,
                    content_type=safe_headers.get("content-type", "text/html").split(";", 1)[0],
                    text=html,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    headers=safe_headers,
                )
                return BrowserRenderResult(fetch=fetch, blocked_requests=blocked)
            finally:
                await browser.close()
