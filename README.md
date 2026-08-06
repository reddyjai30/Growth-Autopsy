# Growth Autopsy POC

This service is the deterministic control plane for Diksha's calendar-to-content
workflow. It gathers public evidence with free local collectors and gives that
fixed evidence corpus to Gemini through Hermes for synthesis. It owns event
parsing, durable state, scheduling, webhook security, deduplication, and
meeting-to-appointment matching.

## Implemented in the first vertical slice

- Google Calendar event parsing with a structured event-description contract
- durable local pre-call scheduling at T-60, with a manual Run now control
- bounded website crawl with robots.txt, sitemap and SSRF protection
- free DuckDuckGo discovery search and on-page SEO/technology signals
- Google PageSpeed with pinned local Lighthouse fallback
- evidence JSON/Markdown and Gemini-synthesized report artifacts
- responsive operator dashboard with progress, downloads and approval controls
- SQLite workflow state and webhook-attempt history
- Fathom webhook signature verification and replay protection
- transcript persistence with speaker names and timestamps
- Fathom-to-calendar matching by time, title, and external attendee email
- post-call Founder Intelligence run submission to Hermes
- local operator API and CLI

## Local setup

```bash
cp .env.example .env
uv sync --extra dev
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

## Required accounts and credentials

1. Enable the Hermes API server and set `API_SERVER_KEY` in the active Hermes
   profile.
2. Complete OAuth for Hermes' bundled `google-workspace` skill and point
   `GA_GOOGLE_TOKEN_FILE` at its authorized-user token.
3. In Fathom Settings → API Access, create a webhook with transcript, summary,
   action items, and CRM matches enabled. Store its `whsec_...` secret as
   `GA_FATHOM_WEBHOOK_SECRET`.
4. Copy the four folders under `skills/` into the active Hermes profile's
   `skills/` directory before scheduling the first call.

Do not expose the Hermes API server directly to the public internet. Only the
Fathom webhook service should be tunneled, and Fathom requests are accepted
only after their timestamped signature validates.

Open `http://127.0.0.1:8787` for the operator dashboard.

## Calendar automation

The web service polls Calendar automatically every 60 seconds. A manual sync is
also available in the dashboard. For one-off diagnostics, run:

```bash
uv run growth-autopsy calendar-sync
```

Use [config/calendar-event-template.md](config/calendar-event-template.md) for
all discovery calls. Missing company or website data moves the appointment to
`NEEDS_INPUT` and deliberately prevents research from being scheduled.

## Current boundary

The POC completes calendar ingestion, pre-call collection/synthesis, report
review controls, Fathom transcript ingestion and Founder Intelligence handoff.
Google Docs, public Notion publishing, LinkedIn queuing, deck export and
two-founder newsletter batching remain later slices. Nothing publishes without
explicit business approval.
