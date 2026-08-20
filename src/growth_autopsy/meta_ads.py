from __future__ import annotations

import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup

from .config import Settings
from .domain import Appointment


META_AD_LIBRARY_BASE = "https://www.facebook.com/ads/library/"
RenderHTML = Callable[[str], Awaitable[str]]


def build_meta_ad_library_url(company: str, country: str = "ALL") -> str:
    query = urlencode(
        {
            "active_status": "active",
            "ad_type": "all",
            "country": country.upper(),
            "q": company,
            "search_type": "keyword_unordered",
            "media_type": "all",
        }
    )
    return f"{META_AD_LIBRARY_BASE}?{query}"


def _clean(value: str, limit: int = 2_000) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _destination_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").removeprefix("www.").casefold()
    if host in {"facebook.com", "l.facebook.com", "lm.facebook.com"}:
        destination = (parse_qs(parsed.query).get("u") or [""])[0].strip()
        if destination.startswith(("http://", "https://")):
            return destination
    return raw_url


def _external_links(container, company_host: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in container.find_all("a", href=True):
        destination = _destination_url(str(anchor.get("href") or ""))
        parsed = urlsplit(destination)
        host = (parsed.hostname or "").removeprefix("www.").casefold()
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if host == "facebook.com" or host.endswith(".facebook.com"):
            continue
        if destination in seen:
            continue
        seen.add(destination)
        links.append(
            {
                "url": destination[:2_000],
                "host": host,
                "matches_company_domain": bool(
                    company_host
                    and (
                        host == company_host
                        or host.endswith(f".{company_host}")
                        or company_host.endswith(f".{host}")
                    )
                ),
            }
        )
    return links[:10]


def _ad_container(node):
    current = node.parent
    best = current
    for _ in range(10):
        if current is None:
            break
        text = _clean(current.get_text(" ", strip=True), 10_000)
        if "Library ID" in text:
            best = current
        if "Library ID" in text and len(text) >= 80 and (
            "Started running" in text or "Active" in text
        ):
            return current
        current = current.parent
    return best


def parse_meta_ad_library_html(
    html: str,
    *,
    search_url: str,
    company_website: str,
    max_ads: int,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean(soup.get_text(" ", strip=True), 100_000)
    company_host = (
        (urlsplit(company_website).hostname or "").removeprefix("www.").casefold()
    )
    blocker_phrases = (
        "you’re temporarily blocked",
        "you're temporarily blocked",
        "security check required",
        "confirm you are human",
        "log into facebook",
    )
    if not re.search(r"Library ID\s*:?\s*\d+", page_text, re.I) and any(
        phrase in page_text.casefold() for phrase in blocker_phrases
    ):
        return {
            "status": "blocked_by_meta",
            "provider": "Meta Ad Library",
            "search_url": search_url,
            "active_ads_observed": None,
            "ads": [],
            "caveat": (
                "Meta did not expose public ad records to the logged-out collector. "
                "Use the official search link for manual verification."
            ),
        }

    ads: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    nodes = soup.find_all(string=re.compile(r"Library ID\s*:?\s*\d+", re.I))
    for node in nodes:
        match = re.search(r"Library ID\s*:?\s*(\d+)", str(node), re.I)
        if not match or match.group(1) in seen_ids:
            continue
        library_id = match.group(1)
        seen_ids.add(library_id)
        container = _ad_container(node)
        text = _clean(container.get_text(" ", strip=True))
        start_match = re.search(
            r"Started running on\s+(.+?)(?=\s+(?:Platforms?|Library ID|Active)\b|$)",
            text,
            re.I,
        )
        platforms = [
            platform
            for platform in ("Facebook", "Instagram", "Messenger", "Audience Network")
            if platform.casefold() in text.casefold()
        ]
        image_count = len(container.find_all("img"))
        video_count = len(container.find_all("video"))
        if video_count:
            creative_format = "video"
        elif image_count > 1:
            creative_format = "multiple_images_or_carousel"
        elif image_count == 1:
            creative_format = "image"
        else:
            creative_format = "not_observable"
        ads.append(
            {
                "library_id": library_id,
                "status": "active" if "active" in text.casefold() else "shown_in_active_search",
                "started_running": _clean(start_match.group(1), 120) if start_match else "",
                "platforms_observed": platforms,
                "creative_format_observed": creative_format,
                "visible_text_excerpt": text,
                "landing_pages": _external_links(container, company_host),
            }
        )
        if len(ads) >= max_ads:
            break

    if ads:
        return {
            "status": "available",
            "provider": "Meta Ad Library",
            "search_url": search_url,
            "active_ads_observed": len(ads),
            "ads": ads,
            "caveat": (
                "This is a bounded snapshot of public active-ad records returned for a "
                "keyword search, not an exhaustive account export. Match advertiser identity "
                "and landing domains before attributing a creative to the company."
            ),
        }

    no_results = any(
        phrase in page_text.casefold()
        for phrase in (
            "no ads match your search criteria",
            "we didn't find any results",
            "we did not find any results",
        )
    )
    return {
        "status": "no_active_ads_observed" if no_results else "inconclusive",
        "provider": "Meta Ad Library",
        "search_url": search_url,
        "active_ads_observed": 0 if no_results else None,
        "ads": [],
        "caveat": (
            "Meta explicitly returned no matching active ads for this search snapshot. "
            "This does not prove the company is not advertising under another Page or country."
            if no_results
            else (
                "No structured Meta ad records could be verified from the public response. "
                "Use the official search link for manual verification; do not report zero ads."
            )
        ),
    }


async def collect_meta_ad_library(
    settings: Settings,
    appointment: Appointment,
    *,
    render_html: RenderHTML | None = None,
) -> dict[str, Any]:
    search_url = build_meta_ad_library_url(
        appointment.company,
        settings.meta_ad_library_country,
    )
    if not settings.meta_ad_library_enabled:
        return {
            "status": "disabled",
            "provider": "Meta Ad Library",
            "search_url": search_url,
            "active_ads_observed": None,
            "ads": [],
        }
    try:
        renderer_callable = render_html
        if renderer_callable is None:
            from .browser import PlaywrightRenderer

            renderer = PlaywrightRenderer(
                timeout_seconds=settings.meta_ad_library_timeout_seconds,
                settle_milliseconds=settings.meta_ad_library_settle_milliseconds,
                max_response_bytes=settings.precall_max_response_bytes,
            )

            async def render_public_page(url: str) -> str:
                result = await renderer.render(url)
                return result.fetch.text

            renderer_callable = render_public_page

        html = await renderer_callable(search_url)
        return parse_meta_ad_library_html(
            html,
            search_url=search_url,
            company_website=appointment.website,
            max_ads=settings.meta_ad_library_max_ads,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "provider": "Meta Ad Library",
            "search_url": search_url,
            "active_ads_observed": None,
            "ads": [],
            "error": str(exc)[:500],
            "caveat": (
                "The official library check could not be completed automatically. Use the "
                "official search link for manual verification; do not infer that ads are absent."
            ),
        }
