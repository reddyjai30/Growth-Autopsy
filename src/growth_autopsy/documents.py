from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_UNORDERED = re.compile(r"^\s*[-+*]\s+(.+?)\s*$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_SAFE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_link(url: str) -> str | None:
    raw = html.unescape(url.strip())
    parsed = urlparse(raw)
    if parsed.scheme.casefold() not in {"http", "https", "mailto"}:
        return None
    return html.escape(raw, quote=True)


def _inline(value: str) -> str:
    """Render a deliberately small, escaped Markdown inline subset."""

    escaped = html.escape(value, quote=False)

    def link(match: re.Match[str]) -> str:
        target = _safe_link(match.group(2))
        if target is None:
            return match.group(1)
        return (
            f'<a href="{target}" target="_blank" rel="noopener noreferrer">'
            f"{match.group(1)}</a>"
        )

    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_to_safe_html(markdown: str) -> str:
    """Convert report Markdown to safe HTML without allowing raw HTML."""

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            parts.append(f"</{list_kind}>")
            list_kind = None

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            language = stripped[3:].strip()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            language_class = (
                f' class="language-{html.escape(language, quote=True)}"'
                if language and re.fullmatch(r"[\w+-]+", language)
                else ""
            )
            parts.append(
                f"<pre><code{language_class}>{html.escape(chr(10).join(code))}</code></pre>"
            )
            index += 1
            continue

        heading = _HEADING.match(line)
        unordered = _UNORDERED.match(line)
        ordered = _ORDERED.match(line)

        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            heading_slug = re.sub(
                r"[^a-z0-9]+", "-", heading.group(2).casefold()
            ).strip("-")
            parts.append(
                f'<h{level} class="section-heading section-{heading_slug}">'
                f"{_inline(heading.group(2))}</h{level}>"
            )
        elif (
            "|" in line
            and index + 1 < len(lines)
            and _TABLE_DIVIDER.match(lines[index + 1])
        ):
            flush_paragraph()
            close_list()
            headers = _table_cells(line)
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_table_cells(lines[index]))
                index += 1
            parts.append("<div class=\"table-wrap\"><table><thead><tr>")
            parts.extend(f"<th>{_inline(cell)}</th>" for cell in headers)
            parts.append("</tr></thead><tbody>")
            for row in rows:
                parts.append("<tr>")
                parts.extend(
                    f"<td>{_inline(row[cell] if cell < len(row) else '')}</td>"
                    for cell in range(len(headers))
                )
                parts.append("</tr>")
            parts.append("</tbody></table></div>")
            continue
        elif unordered or ordered:
            flush_paragraph()
            wanted = "ul" if unordered else "ol"
            if list_kind != wanted:
                close_list()
                parts.append(f"<{wanted}>")
                list_kind = wanted
            match = unordered or ordered
            parts.append(f"<li>{_inline(match.group(1))}</li>")
        elif stripped.startswith(">"):
            flush_paragraph()
            close_list()
            parts.append(f"<blockquote>{_inline(stripped.lstrip('>').strip())}</blockquote>")
        elif re.fullmatch(r"(?:-{3,}|_{3,}|\*{3,})", stripped):
            flush_paragraph()
            close_list()
            parts.append("<hr>")
        elif not stripped:
            flush_paragraph()
            close_list()
        else:
            close_list()
            paragraph.append(stripped)
        index += 1

    flush_paragraph()
    close_list()
    return "\n".join(parts)


def document_filename(company: str, label: str, suffix: str) -> str:
    stem = _SAFE_FILENAME.sub("-", f"{company}-{label}".strip()).strip("-._")
    return f"{stem or 'growth-autopsy-report'}.{suffix.lstrip('.')}"


def render_document_html(
    markdown: str,
    *,
    title: str,
    company: str,
    label: str,
    generated_at: datetime | None,
    include_toolbar: bool = True,
) -> str:
    content = markdown_to_safe_html(markdown)
    date_label = generated_at.astimezone().strftime("%d %B %Y") if generated_at else ""
    toolbar = (
        '<div class="document-toolbar"><span>Growth Autopsy report</span>'
        '<button type="button" onclick="window.print()">Print / Save PDF</button></div>'
        if include_toolbar
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#15221d; --muted:#65726c; --green:#17654d; --green-soft:#e6f3ec; --line:#dfe5e1; --soft:#f5f7f5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:#eef2ef; font:15px/1.7 Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .document-toolbar {{ position:sticky; top:0; z-index:2; height:64px; padding:0 28px; display:flex; align-items:center; justify-content:space-between; color:#eef7f2; background:#102c22; box-shadow:0 5px 22px rgba(12,35,27,.15); }}
    .document-toolbar span {{ font-size:13px; font-weight:700; letter-spacing:.02em; }}
    .document-toolbar button {{ min-height:38px; padding:0 16px; border:0; border-radius:9px; color:#12382b; background:#e6f3ec; font:inherit; font-size:12px; font-weight:800; cursor:pointer; }}
    .page {{ width:min(860px, calc(100% - 32px)); min-height:1120px; margin:32px auto 60px; padding:70px 78px; background:#fff; box-shadow:0 16px 55px rgba(21,45,35,.10); }}
    .document-kicker {{ color:var(--green); font-size:11px; font-weight:800; letter-spacing:.13em; text-transform:uppercase; }}
    .document-title {{ margin:12px 0 7px; font-size:38px; line-height:1.12; letter-spacing:-.035em; }}
    .document-meta {{ margin-bottom:44px; padding-bottom:22px; border-bottom:1px solid var(--line); color:var(--muted); font-size:12px; }}
    article h1 {{ margin:42px 0 16px; font-size:30px; line-height:1.2; letter-spacing:-.025em; }}
    article h2 {{ margin:42px 0 16px; padding:11px 0 10px; border-bottom:1px solid var(--line); color:var(--green); font-size:23px; line-height:1.25; letter-spacing:-.018em; }}
    article h3 {{ margin:28px 0 11px; padding-left:10px; border-left:3px solid #b9d4c8; font-size:17px; }}
    article p {{ margin:0 0 15px; overflow-wrap:anywhere; }}
    article ul, article ol {{ margin:10px 0 22px; padding-left:25px; }}
    article li {{ margin:8px 0; padding-left:5px; overflow-wrap:anywhere; }}
    article li::marker {{ color:var(--green); font-weight:800; }}
    article blockquote {{ margin:22px 0; padding:14px 18px; border-left:4px solid var(--green); color:#3e554b; background:var(--soft); }}
    article code {{ padding:2px 5px; border-radius:5px; background:#edf1ee; font:13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
    article pre {{ overflow:auto; padding:18px; border-radius:10px; color:#eaf4ef; background:#142a21; }}
    article pre code {{ padding:0; color:inherit; background:transparent; }}
    article a {{ color:var(--green); text-underline-offset:3px; }}
    article hr {{ margin:34px 0; border:0; border-top:1px solid var(--line); }}
    .table-wrap {{ margin:20px 0; overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ padding:11px 12px; border:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ color:var(--green); background:var(--soft); }}
    .section-executive-marketing-brief + ul {{ margin:0; padding:15px 19px 15px 38px; border:1px solid #d3e3db; border-radius:10px; background:#f2f8f5; }}
    .section-10-positives + ol, .section-10-growth-gaps + ol, .section-5-discovery-questions + ol {{ padding:0; list-style:none; counter-reset:report-items; }}
    .section-10-positives + ol li, .section-10-growth-gaps + ol li, .section-5-discovery-questions + ol li {{ position:relative; margin:9px 0; padding:13px 15px 13px 45px; border:1px solid var(--line); border-radius:9px; background:#fff; counter-increment:report-items; }}
    .section-10-positives + ol li::before, .section-10-growth-gaps + ol li::before, .section-5-discovery-questions + ol li::before {{ content:counter(report-items); position:absolute; left:13px; top:13px; width:22px; height:22px; border-radius:7px; display:grid; place-items:center; color:var(--green); background:var(--green-soft); font-size:11px; font-weight:800; }}
    .section-10-growth-gaps + ol li {{ border-color:#eadfd9; background:#fdfaf8; }}
    .section-10-growth-gaps + ol li::before {{ color:#965b38; background:#f6e9e2; }}
    .section-5-discovery-questions + ol li {{ border-color:#dae5ec; background:#f7fafc; }}
    .section-5-discovery-questions + ol li::before {{ color:#326b89; background:#e5f0f6; }}
    .section-sources + ul, .section-sources + ol {{ padding:14px 18px 14px 36px; border-radius:9px; color:var(--muted); background:var(--soft); font-size:12px; }}
    @page {{ size:A4; margin:18mm 16mm; }}
    @media print {{ body {{ background:#fff; }} .document-toolbar {{ display:none; }} .page {{ width:auto; min-height:0; margin:0; padding:0; box-shadow:none; }} article h1, article h2, article h3 {{ break-after:avoid; }} article li, article blockquote, table {{ break-inside:avoid; }} }}
    @media (max-width:640px) {{ .document-toolbar {{ padding:0 16px; }} .page {{ width:100%; margin:0; padding:42px 22px; box-shadow:none; }} .document-title {{ font-size:31px; }} }}
  </style>
</head>
<body>
  {toolbar}
  <main class="page">
    <header>
      <div class="document-kicker">{html.escape(label)}</div>
      <h1 class="document-title">{html.escape(title)}</h1>
      <div class="document-meta">{html.escape(company)}{f' · {html.escape(date_label)}' if date_label else ''}</div>
    </header>
    <article>{content}</article>
  </main>
</body>
</html>"""


def resolve_artifact_source(file_path: str, allowed_root: Path) -> Path:
    target = Path(file_path).resolve()
    root = allowed_root.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError("Artifact file not found")
    return target
