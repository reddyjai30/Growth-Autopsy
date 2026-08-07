from datetime import UTC, datetime

import pytest

from growth_autopsy.documents import (
    document_filename,
    markdown_to_safe_html,
    render_document_html,
    resolve_artifact_source,
)


def test_markdown_report_is_structured_and_raw_html_is_escaped() -> None:
    rendered = markdown_to_safe_html(
        """# Acme report

## 10 Positives
1. **Clear positioning** with [public proof](https://example.com/proof).
2. <script>alert('unsafe')</script>

| Signal | Finding |
| --- | --- |
| CTA | Strong |

[unsafe](javascript:alert(1))
"""
    )

    assert '<h1 class="section-heading section-acme-report">Acme report</h1>' in rendered
    assert "<ol>" in rendered
    assert "<strong>Clear positioning</strong>" in rendered
    assert 'href="https://example.com/proof"' in rendered
    assert "<table>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "javascript:" not in rendered


def test_document_page_and_pdf_filename_are_human_readable() -> None:
    page = render_document_html(
        "## 10 Growth Gaps\n\n- Missing lead magnet",
        title="Acme pre-call brief",
        company="Acme & Co",
        label="Pre-call research",
        generated_at=datetime(2026, 8, 7, tzinfo=UTC),
    )

    assert "Acme pre-call brief" in page
    assert "Acme &amp; Co" in page
    assert "Print / Save PDF" in page
    assert document_filename("Acme & Co", "Pre-call research", "pdf") == (
        "Acme-Co-Pre-call-research.pdf"
    )


def test_artifact_source_must_remain_inside_shared_workdir(tmp_path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    report = shared / "report.md"
    report.write_text("# Report", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")

    assert resolve_artifact_source(str(report), shared) == report.resolve()
    with pytest.raises(FileNotFoundError):
        resolve_artifact_source(str(outside), shared)
