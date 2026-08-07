# Growth Autopsy

This service is the deterministic control plane for Diksha's calendar-to-content
workflow. It gathers public evidence with free local collectors and gives that
fixed evidence corpus directly to a configured AI model for synthesis. It owns event
parsing, durable state, scheduling, webhook security, deduplication, and
meeting-to-appointment matching.

The durable product contract is in
[docs/workflow-spec.md](docs/workflow-spec.md).

## Implemented workflow

- Google Calendar event parsing with a structured event-description contract
- durable T-60 evidence collection with a tracked T-30 report deadline and a
  manual Run now control
- bounded Playwright-rendered website crawl with robots.txt, sitemap and SSRF protection
- free DuckDuckGo discovery search; on-page SEO, technology, social, marketplace
  and ad-transparency discovery signals
- Google PageSpeed with pinned local Lighthouse fallback
- durable evidence JSON/Markdown plus a validated, directly synthesized
  pre-call report rendered as polished HTML and cached PDF
- responsive marketing operations dashboard with a nine-stage Jira-style board,
  table view, live progress/analysis states, focused workflow details, browser
  report viewing and PDF export
- SQLite workflow state, webhook-attempt history and a durable workflow audit log
- Fathom webhook signature verification and replay protection
- transcript persistence with speaker names and timestamps
- Fathom-to-calendar matching by time, title, and external attendee email
- Fathom API transcript fallback when a verified webhook omits transcript content
- post-call Founder Intelligence plus conditional Growth Autopsy, 90-day strategy,
  and Gamma-ready pitch-deck brief scaffolding for the next direct-AI phase
- explicit `unsure` strategy-routing pause for Diksha
- approval-gated, idempotent private Notion page creation
- local operator API and CLI

## Local setup

```bash
cp .env.example .env
uv sync --extra dev
uv run playwright install chromium
npm install
uv run growth-autopsy init-db
uv run growth-autopsy serve
```

Run the test suite:

```bash
uv run pytest
```

The service listens on `127.0.0.1:8787` by default. Use a Cloudflare Tunnel or
another HTTPS tunnel for the Fathom destination URL:

```text
https://your-poc-host.example/webhooks/fathom
```

## Phase 1 accounts and credentials

1. Create a dedicated Calendar-read-only token with `growth-autopsy
   calendar-auth`.
2. Configure `GA_AI_BASE_URL`, `GA_AI_API_KEY`, and `GA_AI_MODEL` for an
   OpenAI-compatible model endpoint.

Semrush is optional. Its official API and MCP server consume a qualifying paid
subscription/API units; there is no unrestricted free SEO/traffic API. Leave
`GA_SEMRUSH_API_KEY` empty to use the public Playwright/search baseline without
inventing unavailable traffic data.

For the exact first activation sequence, including regenerating the Google
Desktop OAuth JSON, configuring the direct model, and optionally connecting
Semrush MCP, follow
[docs/phase-1-calendar-precall.md](docs/phase-1-calendar-precall.md).

Fathom and Notion credentials are not required for the Phase 1 live test. They
are configured only when the post-call phase begins.

Open `http://127.0.0.1:8787` for the operator dashboard.

The dashboard reads directly from the workflow SQL database. Each card uses the
Calendar meeting title and moves across the complete Booking → Pre-call → Call →
Transcript → AI Analysis → Case Study → Strategy + Deck → Approval → Notion
workflow. Switch between the Jira-style board and compact table. The meeting
drawer keeps the next action, progress, marketing context, documents and approval
decisions visible while technical activity stays collapsed. Completed reports
open as polished, print-ready HTML and download as cached A4 PDFs; raw Markdown
is not exposed by the UI. The local dashboard opens directly without an access
key. Keep it bound to a trusted machine or protect it at the network edge before
exposing it publicly.

## Calendar automation

The web service polls Calendar automatically every 60 seconds. A manual sync is
also available in the dashboard. For one-off diagnostics, run:

```bash
uv run growth-autopsy calendar-sync
```

Use [config/calendar-event-template.md](config/calendar-event-template.md) for
all discovery calls. `Company Name` and `Company Website` are required. Founder
email, founder LinkedIn and the meeting agenda are optional inputs used to enrich
the report. Missing company or website data moves the appointment to
`NEEDS_INPUT` and deliberately prevents research from being scheduled.

## Current boundary

The service completes Calendar booking resolution, T-30 pre-call research,
Fathom transcript ingestion, Founder Intelligence, conditional document
generation, Diksha approval, and private Notion package creation. Licensed
Similarweb/Semrush/Ahrefs data is not fabricated when unavailable. Final Gamma
export, founder-email consent, public Notion publishing, LinkedIn queuing, and
two-founder newsletter batching remain later slices. Nothing public is released
without founder consent.

See [docs/production-runbook.md](docs/production-runbook.md) for environment and
webhook setup.
