from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from growth_autopsy.browser import BrowserRenderResult, PlaywrightRenderer
from growth_autopsy.config import Settings
from growth_autopsy.domain import Appointment, AppointmentStatus
from growth_autopsy.research import FetchResult, FreePrecallResearcher


def appointment() -> Appointment:
    return Appointment(
        calendar_event_id="event-1",
        calendar_id="primary",
        etag="1",
        title="[GROWTH AUTOPSY] Acme",
        company="Acme",
        website="https://acme.example",
        founder_name="Alice",
        founder_email="",
        founder_linkedin="",
        industry="Ecommerce",
        strategy_mode="auto",
        start_at=datetime(2026, 8, 7, 10, tzinfo=UTC),
        end_at=datetime(2026, 8, 7, 11, tzinfo=UTC),
        status=AppointmentStatus.RESEARCH_SCHEDULED,
        source_payload={},
        meeting_agenda="Discuss acquisition and conversion",
    )


@pytest.mark.asyncio
async def test_free_research_collects_observed_signals(tmp_path) -> None:
    homepage = """
    <html lang="en"><head><title>Acme — Better shoes</title>
    <meta name="description" content="Comfortable shoes for remote teams">
    <meta name="viewport" content="width=device-width"></head>
    <body><h1>Better shoes for remote teams</h1><a href="/pricing">See pricing</a>
    <a href="/case-studies">Customer stories</a><button>Shop now</button>
    <a href="https://instagram.com/acme">Instagram</a>
    <a href="https://linkedin.com/company/acme">LinkedIn</a>
    <form><input type="email"></form><p>Trusted by 1,000 teams</p>
    <script>fbq('init', '123'); ttq.load('456')</script></body></html>
    """
    pricing = "<html><head><title>Acme pricing</title></head><body><h1>Plans</h1><button>Buy now</button></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"lighthouseResult": {"categories": {"performance": {"score": .91}, "seo": {"score": 1}}, "audits": {}, "fetchTime": "2026-08-06T00:00:00Z", "lighthouseVersion": "13"}})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nSitemap: https://acme.example/sitemap.xml")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text="<urlset><url><loc>https://acme.example/pricing</loc></url></urlset>")
        if request.url.path == "/pricing":
            return httpx.Response(200, text=pricing, headers={"content-type": "text/html"})
        if request.url.path == "/case-studies":
            return httpx.Response(200, text="<html><title>Customers</title><h1>Customer stories</h1></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=homepage, headers={"content-type": "text/html"})

    async def search(query: str, limit: int) -> list[dict[str, str]]:
        del limit
        if "competitors alternatives" in query:
            return [{"title": "Beta Shoes", "url": "https://beta.example", "snippet": query}]
        return [{"title": "Acme review", "url": "https://review.example/acme", "snippet": query}]

    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        lighthouse_executable=tmp_path / "lighthouse",
        precall_max_pages=4,
        semrush_mcp_enabled=False,
        semrush_api_key="",
    ).resolve_paths(tmp_path)
    researcher = FreePrecallResearcher(
        settings,
        transport=httpx.MockTransport(handler),
        search=search,
        validate_network=False,
    )

    evidence = await researcher.collect(appointment())

    summary = evidence["website"]["site_summary"]
    assert summary["pricing_page_observed"] is True
    assert summary["case_study_or_customer_page_observed"] is True
    assert summary["email_capture_observed"] is True
    assert evidence["pagespeed"]["mobile"]["scores"]["performance"] == 91
    assert evidence["traffic"]["estimated_monthly_visits"] is None
    assert evidence["public_search"]["queries"][0]["results"]
    assert len(evidence["public_search"]["queries"]) == 8
    assert evidence["channels"]["channels"]["instagram"]["status"] == "observed_from_company_website"
    assert evidence["channels"]["channels"]["linkedin"]["status"] == "observed_from_company_website"
    assert evidence["ads"]["tracking_technology_observed"] == ["Meta Pixel", "TikTok Pixel"]
    assert evidence["ads"]["meta"]["status"] == "official_library_verification_required"
    assert evidence["competitors"]["candidates"][0]["host"] == "beta.example"
    assert evidence["appointment"]["meeting_agenda"] == (
        "Discuss acquisition and conversion"
    )


@pytest.mark.asyncio
async def test_free_research_uses_browser_when_direct_homepage_is_blocked(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        precall_search_enabled=False,
        precall_pagespeed_enabled=False,
        semrush_mcp_enabled=False,
        semrush_api_key="",
        playwright_enabled=True,
    ).resolve_paths(tmp_path)
    researcher = FreePrecallResearcher(settings, validate_network=False)

    async def blocked_fetch(raw_url: str, *, html_only: bool = False) -> FetchResult:
        del html_only
        return FetchResult(
            requested_url=raw_url,
            final_url=raw_url,
            status_code=403,
            content_type="text/html",
            text="<html><title>Forbidden</title></html>",
            elapsed_ms=5,
            headers={"content-type": "text/html"},
        )

    rendered_html = """
    <html><head><title>Acme — Browser rendered</title></head>
    <body><h1>Better shoes</h1><a href="/shop">Shop now</a></body></html>
    """

    async def rendered_fetch(
        self: PlaywrightRenderer, raw_url: str
    ) -> BrowserRenderResult:
        del self
        return BrowserRenderResult(
            fetch=FetchResult(
                requested_url=raw_url,
                final_url=raw_url,
                status_code=200,
                content_type="text/html",
                text=rendered_html,
                elapsed_ms=12,
                headers={"content-type": "text/html"},
            ),
            blocked_requests=3,
        )

    monkeypatch.setattr(researcher.fetcher, "fetch", blocked_fetch)
    monkeypatch.setattr(PlaywrightRenderer, "render", rendered_fetch)

    evidence = await researcher.collect(appointment())

    assert evidence["website"]["browser_render"]["status"] == "available"
    assert evidence["website"]["homepage"]["status_code"] == 200
    assert evidence["website"]["site_summary"]["pages_successfully_analyzed"] == 1
    assert any("returned HTTP 403" in item for item in evidence["warnings"])


@pytest.mark.asyncio
async def test_free_research_continues_when_homepage_cannot_be_rendered(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html><title>Forbidden</title></html>",
            headers={"content-type": "text/html"},
        )

    settings = Settings(
        database_path=tmp_path / "state.db",
        shared_workdir=tmp_path,
        precall_search_enabled=False,
        precall_pagespeed_enabled=False,
        semrush_mcp_enabled=False,
        semrush_api_key="",
        playwright_enabled=True,
    ).resolve_paths(tmp_path)
    researcher = FreePrecallResearcher(
        settings,
        transport=httpx.MockTransport(handler),
        validate_network=False,
    )

    evidence = await researcher.collect(appointment())

    assert evidence["website"]["homepage"]["status"] == "unavailable"
    assert evidence["website"]["site_summary"]["pages_successfully_analyzed"] == 0
    assert evidence["website"]["crawl_errors"][0]["status"] == "homepage_unavailable"
    assert any("research continued" in item for item in evidence["warnings"])
