# Production runbook

## 1. Runtime installation

```bash
cp .env.example .env
uv sync --extra dev
uv run playwright install chromium
npm install
uv run growth-autopsy init-db
```

Run the controller from the repository root so the resolved database, token,
Lighthouse, transcript and artifact paths remain stable.

## 2. Google Calendar

Enable the Google Calendar API, download a Desktop OAuth client JSON, and run
`uv run growth-autopsy calendar-auth --client-secret /path/to/client.json`.
This creates a dedicated authorized-user token at `GA_GOOGLE_TOKEN_FILE` with
Calendar read-only scope. The event must use the configured title prefix or the
legacy `Automation: GROWTH_AUTOPSY` marker. `Company Name` and `Company Website`
are required; founder email, founder LinkedIn, and a multi-line meeting agenda
are optional. See
[`phase-1-calendar-precall.md`](phase-1-calendar-precall.md) for the exact setup
and verification sequence.

The Calendar event ID is the workflow correlation key. The service stores the
event's `iCalUID` and Google Meet entry point as audit evidence. Fathom is matched
using its scheduled time, Calendar title and external invitee email because the
official Fathom webhook does not expose the Google Calendar event ID.

## 3. Direct AI synthesis

Set `GA_AI_BASE_URL`, `GA_AI_API_KEY`, and `GA_AI_MODEL` for an
OpenAI-compatible Chat Completions endpoint. The service sends the bounded,
persisted evidence JSON directly to that endpoint; no agent runtime is part of
the Phase 1 flow.

The Growth Autopsy service owns scheduling, evidence collection, state and the
full prompt/evidence boundary. Company-specific claims must come from the saved
evidence pack.

## 4. Public research and Semrush

The default baseline uses disposable Playwright Chromium contexts, a bounded
same-site crawl, robots/sitemap evidence, DuckDuckGo discovery and
PageSpeed/Lighthouse. Private/reserved destinations are blocked and public page
content is treated as untrusted evidence.

Semrush is strictly optional. Set `GA_SEMRUSH_MCP_ENABLED=true` and
`GA_SEMRUSH_API_KEY` only for an account with the appropriate official API
entitlement and units. The service connects directly to the official Streamable
HTTP MCP endpoint and executes at most `GA_SEMRUSH_MCP_MAX_REPORTS` bounded,
read-only reports. Failures remain explicit and the pipeline continues with
public evidence.

## 5. Fathom

Create a Fathom webhook pointing to:

```text
https://your-public-controller.example/webhooks/fathom
```

For a local Fathom test, authenticate the installed ngrok agent once and start a
path-restricted tunnel:

```bash
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
ngrok http 8787 --traffic-policy-file config/ngrok-fathom-policy.yml
```

Use the HTTPS forwarding address followed by `/webhooks/fathom` as the Fathom
destination URL. The committed traffic policy returns `404` for every public
request except `POST /webhooks/fathom`, so the local dashboard and internal APIs
are not exposed through this test tunnel. Keep the application itself bound to
`127.0.0.1`.

Enable transcript, summary and action items. Store the returned `whsec_...`
secret in `GA_FATHOM_WEBHOOK_SECRET`. Configure `GA_FATHOM_API_KEY` so the
controller can retrieve `/recordings/{recording_id}/transcript` when a verified
webhook contains no transcript. Only this signature-verified endpoint needs to
be publicly reachable.

## 6. Notion

Create an internal Notion connection, grant it insert-content/property
capabilities, and share the chosen parent page with it. Configure
`GA_NOTION_API_KEY` and `GA_NOTION_PARENT_PAGE_ID`.

The controller creates one private Markdown child page only after all required
artifacts are approved. A case-study-only call requires the Growth Autopsy
approval. A strategy call requires Growth Autopsy, strategy and pitch-deck brief
approvals. The stored Notion page ID makes retries idempotent.

## 7. Start and verify

```bash
uv run pytest
uv run growth-autopsy calendar-sync
uv run growth-autopsy serve --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`, confirm the Calendar sync timestamp, and run one
test event manually before enabling the background loop for live calls.
