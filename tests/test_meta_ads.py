from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from growth_autopsy.config import Settings
from growth_autopsy.domain import Appointment, AppointmentStatus
from growth_autopsy.meta_ads import (
    build_meta_ad_library_url,
    collect_meta_ad_library,
    parse_meta_ad_library_html,
)


def appointment() -> Appointment:
    return Appointment(
        calendar_event_id="meta-event",
        calendar_id="primary",
        etag="1",
        title="Founder conversation",
        company="Gymshark",
        website="https://uk.gymshark.com/",
        founder_name="Ben Francis MBE",
        founder_email="",
        founder_linkedin="https://www.linkedin.com/in/gymshark/",
        industry="DTC Fitness Apparel and Accessories",
        strategy_mode="auto",
        start_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        status=AppointmentStatus.BOOKED,
        source_payload={},
    )


def test_meta_search_url_uses_company_and_active_commercial_filters() -> None:
    url = urlsplit(build_meta_ad_library_url("Gymshark", "GB"))
    query = parse_qs(url.query)

    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://www.facebook.com/ads/library/"
    )
    assert query["active_status"] == ["active"]
    assert query["ad_type"] == ["all"]
    assert query["country"] == ["GB"]
    assert query["q"] == ["Gymshark"]


def test_meta_parser_extracts_bounded_active_ad_evidence() -> None:
    html = """
    <html><body>
      <div class="ad-card">
        <div>Active</div><span>Library ID: 123456</span>
        <div>Started running on 12 Aug 2026 Platforms Facebook Instagram</div>
        <p>Train for more. New performance collection available now. Shop the drop today.</p>
        <img src="creative.jpg" alt="Performance collection">
        <a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fuk.gymshark.com%2Fcollections%2Fnew">Shop now</a>
      </div>
      <div class="ad-card">
        <div>Active</div><span>Library ID: 789012</span>
        <div>Started running on 15 Aug 2026 Platforms Instagram</div>
        <p>Built for your strongest session. Discover the latest training essentials.</p>
        <video src="creative.mp4"></video>
        <a href="https://example.org/campaign">Learn more</a>
      </div>
    </body></html>
    """
    result = parse_meta_ad_library_html(
        html,
        search_url=build_meta_ad_library_url("Gymshark"),
        company_website="https://uk.gymshark.com/",
        max_ads=10,
    )

    assert result["status"] == "available"
    assert result["active_ads_observed"] == 2
    assert result["ads"][0]["library_id"] == "123456"
    assert result["ads"][0]["creative_format_observed"] == "image"
    assert result["ads"][0]["landing_pages"][0]["matches_company_domain"] is True
    assert result["ads"][1]["creative_format_observed"] == "video"
    assert result["ads"][1]["landing_pages"][0]["matches_company_domain"] is False


@pytest.mark.asyncio
async def test_meta_collector_preserves_inconclusive_state_without_inventing_zero() -> None:
    settings = Settings(meta_ad_library_enabled=True)

    async def render_html(url: str) -> str:
        assert "q=Gymshark" in url
        return "<html><body>Ad Library search is loading.</body></html>"

    result = await collect_meta_ad_library(
        settings,
        appointment(),
        render_html=render_html,
    )

    assert result["status"] == "inconclusive"
    assert result["active_ads_observed"] is None
    assert "do not report zero ads" in result["caveat"]


@pytest.mark.asyncio
async def test_meta_collector_marks_login_wall_as_blocked_not_zero() -> None:
    settings = Settings(meta_ad_library_enabled=True)

    async def render_html(url: str) -> str:
        return "<html><body>Log into Facebook to continue</body></html>"

    result = await collect_meta_ad_library(
        settings,
        appointment(),
        render_html=render_html,
    )

    assert result["status"] == "blocked_by_meta"
    assert result["active_ads_observed"] is None
    assert result["search_url"].startswith("https://www.facebook.com/ads/library/")


@pytest.mark.asyncio
async def test_meta_collector_reports_explicit_no_results_carefully() -> None:
    settings = Settings(meta_ad_library_enabled=True)

    async def render_html(url: str) -> str:
        return "<html><body>No ads match your search criteria</body></html>"

    result = await collect_meta_ad_library(
        settings,
        appointment(),
        render_html=render_html,
    )

    assert result["status"] == "no_active_ads_observed"
    assert result["active_ads_observed"] == 0
    assert "another Page or country" in result["caveat"]
