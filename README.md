# Growth Autopsy

Growth Autopsy is a local-first automation service for the complete founder discovery workflow:

```text
Google Calendar booking
        ↓
T-60 public website, SEO, performance, social, advertising, and optional Semrush research
        ↓
T-30 polished pre-call report
        ↓
Discovery call and Fathom transcript
        ↓
Direct AI analysis and document generation
        ↓
Founder Intelligence → Growth Intelligence Report → approval-derived LinkedIn post
        + conditional one-problem Strategy → approval-derived Pitch Deck
        ↓
Human approval
        ↓
Private Notion page → approved post on the connected LinkedIn profile
```

The application includes a Jira-style pipeline dashboard, a compact table view, HTML document previews, PDF downloads, SQLite persistence, secure webhook processing, and an approval/revision workflow.

The workflow contract is documented in [docs/workflow-spec.md](docs/workflow-spec.md).
The production EC2 deployment sequence is documented in
[docs/deploy-ec2.md](docs/deploy-ec2.md).

## What is implemented

- Read-only Google Calendar polling and structured event parsing
- automatic T-60 research collection and T-30 report delivery tracking
- Playwright-rendered website crawling with robots.txt, sitemap, size, concurrency, and SSRF safeguards
- public search, on-page SEO, technology, social, marketplace, and advertising-transparency discovery
- Google PageSpeed collection with a local Lighthouse fallback
- optional official Semrush MCP enrichment when the account has eligible API access and units
- direct OpenAI-compatible AI synthesis for pre-call and post-call documents
- polished HTML report previews and cached A4 PDF downloads
- Fathom webhook signature verification, replay protection, transcript storage, and Calendar matching
- Fathom API transcript fallback when the webhook does not contain a transcript
- evidence-led Founder Intelligence with public-safety and one-problem routing ledgers
- 14-section Growth Intelligence Report v2 with strict evidence and tone validation
- approval-derived LinkedIn Growth Autopsy post with a public-claim ledger
- conditional one-problem Strategy Doc and approval-derived 13/14-slide Pitch Deck
- an explicit human decision when strategy intent is uncertain
- approve, reject, revise, and regenerate controls
- approval-gated, idempotent private Notion publishing
- OAuth-based, approval-gated personal LinkedIn publishing after Notion succeeds
- a responsive pipeline dashboard backed by SQLite
- a local-only admin console with a read-only database browser and masked configuration editor
- a CLI for setup, diagnostics, synchronization, and serving the application

Hermes Agent is not required. The application owns the deterministic workflow and calls the configured AI endpoint directly.

## Prerequisites

Install these before cloning the repository:

| Requirement | Supported/recommended version | Used for |
| --- | --- | --- |
| Git | current | cloning and updates |
| Python | 3.11–3.13; 3.11 is pinned by this repository | application runtime |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | current | Python and dependency management |
| Node.js | 22.19 or newer | local Lighthouse |
| npm | bundled with Node.js | Lighthouse installation |
| [ngrok](https://ngrok.com/docs/getting-started/) | current | temporary public Fathom webhook URL |

Install `uv` on macOS with Homebrew:

```bash
brew install uv
```

Or use the official macOS/Linux installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

If `uv` is still reported as unavailable after installation, open a new terminal and run `uv --version`.

Install ngrok on macOS:

```bash
brew install ngrok
```

## 1. Clone and install

Run these commands from the directory where you want the project:

```bash
git clone https://github.com/reddyjai30/Growth-Autopsy.git
cd Growth-Autopsy

uv python install 3.11
uv sync --extra dev
uv run playwright install chromium
npm ci

cp .env.example .env
uv run growth-autopsy init-db
```

For a deployment that must use the exact committed dependency lock, replace `uv sync --extra dev` with:

```bash
uv sync --frozen --extra dev
```

Do not run `source .env`. Some values, such as the Calendar title prefix, contain spaces. The application loads `.env` itself.

The following local files are intentionally ignored by Git:

- `.env`
- `secrets/`
- the SQLite database under `data/`
- transcripts, evidence, reports, generated documents, and PDFs under `data/`

Never commit API keys, OAuth files, webhook secrets, transcripts, or client reports.

## 2. Configure the environment

Open `.env` in a text editor. The minimum settings needed for Calendar and AI processing are:

```dotenv
GA_ENVIRONMENT=development
GA_DATABASE_PATH=./data/growth_autopsy.db
GA_SHARED_WORKDIR=./data

# Required only when GA_ENVIRONMENT=production
GA_APP_USERNAME=
GA_APP_PASSWORD=
GA_SESSION_SECRET=
GA_SESSION_TTL_HOURS=12

GA_GOOGLE_CALENDAR_ID=primary
GA_GOOGLE_TOKEN_FILE=./secrets/google-token.json
GA_CALENDAR_TITLE_PREFIX=[GROWTH AUTOPSY]
GA_DIKSHA_EMAIL=
GA_CALENDAR_LOOKAHEAD_DAYS=30
GA_CALENDAR_LOOKBACK_HOURS=24
GA_PRECALL_START_MINUTES=60
GA_PRECALL_DELIVERY_MINUTES=30

GA_AI_BASE_URL=https://api.openai.com/v1
GA_AI_API_KEY=replace-with-your-key
GA_AI_MODEL=gpt-5.4-mini
GA_AI_TIMEOUT_SECONDS=120
GA_AI_MAX_OUTPUT_TOKENS=6000

GA_ENABLE_BACKGROUND_SYNC=true
GA_BACKGROUND_SYNC_INTERVAL_SECONDS=60
```

The AI endpoint must implement an OpenAI-compatible Chat Completions API. Keep the model name configurable because availability depends on the provider and account.

The application reads `.env` at startup. Restart the server after changing any environment value.

### Optional research settings

The defaults in `.env.example` enable the free local collectors. Important controls are:

```dotenv
GA_PRECALL_MAX_PAGES=12
GA_PRECALL_MAX_CONCURRENCY=4
GA_PRECALL_SEARCH_ENABLED=true
GA_PRECALL_PAGESPEED_ENABLED=true
GA_PAGESPEED_API_KEY=
GA_PRECALL_LOCAL_LIGHTHOUSE_ENABLED=true
GA_LIGHTHOUSE_EXECUTABLE=./node_modules/.bin/lighthouse
GA_PLAYWRIGHT_ENABLED=true
```

`GA_PAGESPEED_API_KEY` is optional. Without it, quota is more limited and local Lighthouse remains available as a fallback.

### Optional Semrush MCP settings

Semrush is enrichment, not a requirement:

```dotenv
GA_SEMRUSH_API_KEY=
GA_SEMRUSH_DATABASE=us
GA_SEMRUSH_COUNTRY=us
GA_SEMRUSH_MCP_ENABLED=false
GA_SEMRUSH_MCP_URL=https://mcp.semrush.com/v2/mcp
GA_SEMRUSH_MCP_MAX_REPORTS=3
```

The official Semrush API/MCP requires an account with eligible API access and can consume API units. Connecting Semrush in another application, such as Claude, does not give this service access to that connection. Add a valid key and set `GA_SEMRUSH_MCP_ENABLED=true` only when the Semrush account supports the requested reports.

Verify the configured Semrush connection with:

```bash
uv run growth-autopsy semrush-check
```

If Semrush is disabled or unavailable, the pipeline continues with public evidence and does not invent traffic, keyword, backlink, CPA, ROAS, or conversion data.

## 3. Create Google Calendar OAuth credentials

The application uses a Google Desktop OAuth client and requests read-only Calendar access.

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Select or create a project.
3. Enable **Google Calendar API**.
4. Configure the OAuth consent screen. If the application is in testing mode, add the Google account that owns the Calendar as a test user.
5. Go to **APIs & Services → Credentials**.
6. Select **Create credentials → OAuth client ID**.
7. Choose **Desktop app**.
8. Download the JSON file.
9. Save it outside Git, for example as `secrets/google-oauth-client.json`.

Generate the local read-only token:

```bash
mkdir -p secrets
uv run growth-autopsy calendar-auth --client-secret ./secrets/google-oauth-client.json
```

A browser opens for Google authorization. On success, the generated token is written to the path configured by `GA_GOOGLE_TOKEN_FILE`, normally:

```text
./secrets/google-token.json
```

Check the connection:

```bash
uv run growth-autopsy calendar-check
```

If a token expires, is revoked, or was created for the wrong Google account, regenerate it:

```bash
uv run growth-autopsy calendar-auth \
  --client-secret ./secrets/google-oauth-client.json \
  --force
```

## 4. Create a discovery meeting

Use this title format so the Calendar poller recognizes the meeting:

```text
[GROWTH AUTOPSY] Company Name – Founder Name
```

Use this description format:

```text
Company Name: Company Name
Company Website: https://www.example.com
Founder Email: founder@example.com
Founder LinkedIn: https://www.linkedin.com/in/founder-name

Meeting Agenda:
- Understand the company, audience, offer, and current growth channels
- Discuss the founder's main growth constraints and targets
- Identify opportunities for the Growth Autopsy
```

`Company Name` and `Company Website` are required. Founder email, LinkedIn, and agenda are optional but improve matching and analysis. The reusable template is at [config/calendar-event-template.md](config/calendar-event-template.md).

After creating the event, force a synchronization:

```bash
uv run growth-autopsy calendar-sync
uv run growth-autopsy status --limit 20
```

With background synchronization enabled, the running server checks Calendar every 60 seconds. Research begins at T-60 and the report is targeted for T-30. The dashboard also provides a manual **Run now** control for testing outside that window.

## 5. Run the application

Start the server from the repository root:

```bash
uv run growth-autopsy serve --host 127.0.0.1 --port 8787
```

Open:

- Dashboard: [http://127.0.0.1:8787](http://127.0.0.1:8787)
- Admin console: [http://127.0.0.1:8787/admin/](http://127.0.0.1:8787/admin/)
- Health check: [http://127.0.0.1:8787/health](http://127.0.0.1:8787/health)

You can also test the health endpoint from another terminal:

```bash
curl http://127.0.0.1:8787/health
```

The dashboard intentionally has no application login. Keep it bound to `127.0.0.1`; do not expose the entire application or `/internal/*` routes directly to the public internet.

## 6. Configure Fathom

Fathom sends the completed call to:

```text
POST /webhooks/fathom
```

Add the following values to `.env`:

```dotenv
GA_FATHOM_WEBHOOK_SECRET=whsec_replace_with_fathom_signing_secret
GA_FATHOM_API_KEY=replace_with_fathom_api_key
GA_FATHOM_MATCH_WINDOW_MINUTES=20
GA_MAX_FATHOM_WEBHOOK_BYTES=5242880
```

`GA_FATHOM_WEBHOOK_SECRET` validates webhook signatures. `GA_FATHOM_API_KEY` is separate and lets the application retrieve a transcript when the verified webhook does not include it.

### Create a safe local webhook URL with ngrok

Authenticate ngrok once using the token shown in the ngrok dashboard:

```bash
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
ngrok config check
```

Do not add the ngrok authentication token to this project's `.env`.

Keep the application running in terminal 1:

```bash
uv run growth-autopsy serve --host 127.0.0.1 --port 8787
```

Start the restricted tunnel in terminal 2:

```bash
ngrok http 8787 --traffic-policy-file config/ngrok-fathom-policy.yml
```

The committed traffic policy permits only `POST /webhooks/fathom` and rejects public dashboard/internal API requests. Copy the HTTPS forwarding address printed by ngrok and append the webhook path:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.app/webhooks/fathom
```

In [Fathom](https://fathom.video/):

1. Create an API key under user settings/API access and place it in `GA_FATHOM_API_KEY`.
2. Create a webhook using the HTTPS URL above.
3. Subscribe to completed recordings/meetings.
4. Include transcript, summary, and action items when those webhook options are available.
5. Copy the webhook signing secret into `GA_FATHOM_WEBHOOK_SECRET`.
6. Restart the Growth Autopsy server.

A valid webhook is acknowledged with HTTP `202`. Use ngrok's local inspector at [http://127.0.0.1:4040](http://127.0.0.1:4040) to inspect delivery status during development.

The service deduplicates retried deliveries, stores the transcript, and matches it to a Calendar appointment by call time, title, and external attendee email.

## 7. Configure Notion publishing

Notion publishing uses the official API; a Notion MCP connection is not required.

1. Create an internal integration at [Notion integrations](https://www.notion.so/profile/integrations).
2. Give the integration permission to insert content. If Notion shows a property capability, also allow inserting/updating properties.
3. Open the Notion page that should contain the generated client pages.
4. Share that parent page with the integration using **Connections** or **Add connections**.
5. Copy the integration secret and the parent page ID.

Add these values to `.env`:

```dotenv
GA_NOTION_API_KEY=ntn_replace_with_notion_integration_secret
GA_NOTION_PARENT_PAGE_ID=replace_with_parent_page_id
GA_NOTION_API_VERSION=2026-03-11
GA_NOTION_PUBLISH_AFTER_APPROVAL=true
```

Restart the server. After a document package is approved in the dashboard, publishing creates a private child page under the configured parent and records the page ID/URL so retries do not create duplicates.

## 8. Configure personal LinkedIn publishing (currently paused)

The LinkedIn generation and publishing lane is disabled by default while its
post contract and OAuth setup are being finalized. Keep
`GA_LINKEDIN_ENABLED=false`; approved workflows will publish to Notion without
creating, requiring, retrying, or publishing a LinkedIn artifact.

TODO: When LinkedIn is ready to resume, complete the setup below and change
`GA_LINKEDIN_ENABLED=true`.

Create or open a LinkedIn Developer app associated with the profile/business that owns this installation. In the app:

1. Enable **Share on LinkedIn** so the app can request `w_member_social`.
2. Enable **Sign In with LinkedIn using OpenID Connect** so the app can identify the authorized member using `openid profile`.
3. Add this exact authorized redirect URL:

```text
http://localhost:8787/internal/linkedin/oauth/callback
```

Copy the app's Client ID and Client Secret into Admin → Configuration → LinkedIn, or set:

```dotenv
GA_LINKEDIN_CLIENT_ID=replace_with_client_id
GA_LINKEDIN_CLIENT_SECRET=replace_with_client_secret
GA_LINKEDIN_REDIRECT_URI=http://localhost:8787/internal/linkedin/oauth/callback
GA_LINKEDIN_TOKEN_FILE=./secrets/linkedin-token.json
GA_LINKEDIN_API_VERSION=202607
GA_LINKEDIN_ENABLED=false
GA_LINKEDIN_PUBLISH_AFTER_NOTION=true
```

Restart the application, reopen [Admin → Configuration](http://127.0.0.1:8787/admin/#configuration), and select **Connect LinkedIn**. Sign in to the personal profile that should publish the posts and approve access. The application writes the access token to `./secrets/linkedin-token.json` with local-user-only permissions; neither the token nor the client secret is returned to the admin browser.

Once enabled, the final approval triggers this order:

```text
all required documents approved
  → private Notion page succeeds
  → the approved Draft Post section is extracted without internal ledgers
  → that exact public text is published to the connected personal profile
  → Notion page ID and LinkedIn post URN/URL are stored for duplicate protection
```

If LinkedIn returns an uncertain network outcome, the automation does not retry blindly. It pauses and offers two explicit dashboard actions: record the post URL if the post exists, or confirm that the profile was checked and retry once if it does not. LinkedIn access tokens expire; reconnect the profile from Admin when the status changes to **Expired**.

This publishes the approved text post only. It does not create the “full Growth Autopsy” comment, make the private Notion page public, attach a document/carousel, or send founder email. Add any public report link/comment separately. Only enable automatic LinkedIn publishing after founder/public-content consent is part of your approval process.

## 9. Use the dashboard

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). The board tracks each meeting through:

```text
Booking → Pre-call → Discovery Call → Transcript → AI Analysis
        → Growth Intelligence Report → Strategy + share assets → Approval
        → Notion → LinkedIn
```

Each card is titled with the Calendar meeting name. Open a card to see the next action, current progress, founder/company context, generated documents, approvals, and a collapsed technical activity log. Completed reports open as formatted HTML and can be downloaded as A4 PDFs; raw Markdown is not exposed in the UI.

If the AI confidently detects a strategy discussion, the approval-gated sequence is:

1. Founder Intelligence document
2. Growth Intelligence Report v2
3. One-problem Strategy Doc
4. After report approval: LinkedIn Growth Autopsy post
5. After strategy approval: Gamma-ready Pitch Deck

The report keeps facts, praise, diagnosis, opportunities, MMS recommendations and
shareable lessons separate. The Strategy Doc routes only from the founder's stated
problem; it never invents commercial inputs. Paid-media decks contain 14 slides,
secondary-lane decks contain 13, and pricing remains on the final slide.

If no strategy discussion occurred, only the applicable non-strategy documents are created. If the routing decision is uncertain, the pipeline pauses for a human choice.

## 10. Use the admin console

Open [http://127.0.0.1:8787/admin/](http://127.0.0.1:8787/admin/) or select **Admin** in the dashboard header.

The **Database** section provides a MongoDB-style collection browser for the application's allowlisted SQLite tables. It supports record counts, full-field search, pagination, and a structured record inspector. Every database request uses a separate query-only connection; the admin console cannot insert, update, or delete database records.

The **Configuration** section edits the supported `GA_*` values in the repository's `.env` file. API keys and other secrets are never returned to the browser: the UI shows only whether a secret is configured, and a blank secret field means “keep the current value.” Clearing a saved secret requires the explicit **Clear saved** action. Saves are atomic and the resulting `.env` file is restricted to the local user. Restart the application after saving configuration so the running scheduler and integrations receive the new values.

You can also upload a Google **Desktop app** OAuth JSON from the Configuration page. The file is validated and saved as:

```text
./secrets/google-oauth-client.json
```

The browser does not generate or expose a Google access token. After uploading, copy the command shown in the console and run it from the repository root:

```bash
uv run growth-autopsy calendar-auth --client-secret ./secrets/google-oauth-client.json
```

The generated Calendar token is saved to `GA_GOOGLE_TOKEN_FILE`, normally `./secrets/google-token.json`.

The admin APIs reject non-loopback clients. Keep the service bound to `127.0.0.1`; the ngrok traffic policy intentionally blocks the admin console and all `/internal/*` routes.

LinkedIn OAuth also returns to localhost and must be completed from the same Mac running the application. Do not use the ngrok Fathom webhook URL as the LinkedIn redirect URL.

## CLI reference

Run all commands from the repository root:

| Command | Purpose |
| --- | --- |
| `uv run growth-autopsy init-db` | create or migrate the local SQLite database |
| `uv run growth-autopsy calendar-auth --client-secret PATH` | authorize Google Calendar read-only access |
| `uv run growth-autopsy calendar-auth --client-secret PATH --force` | replace an existing Google token |
| `uv run growth-autopsy calendar-check` | verify Calendar credentials and show recognized events |
| `uv run growth-autopsy calendar-sync` | immediately import/update Calendar events |
| `uv run growth-autopsy semrush-check` | test optional Semrush MCP access |
| `uv run growth-autopsy status --limit 20` | display recent workflow records in the terminal |
| `uv run growth-autopsy serve --host 127.0.0.1 --port 8787` | run dashboard, API, scheduler, and webhook service |

## Development and verification

Install development dependencies with `uv sync --extra dev`, then run:

```bash
uv run pytest
uv run python -m compileall -q src tests
node --check src/growth_autopsy/dashboard/app.js
node --check src/growth_autopsy/dashboard/admin/admin.js
```

Before committing a change, also review the worktree:

```bash
git status --short
git diff --check
```

## Troubleshooting

### `zsh: command not found: uv`

Install uv using the prerequisite instructions, open a new terminal, and verify:

```bash
uv --version
```

### Port 8787 is already in use

On macOS/Linux, identify the process:

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

Stop the intended old process or start this application on another port. If the port changes, use the same port in the ngrok command.

### Calendar event is not appearing

- confirm `calendar-check` succeeds
- confirm the title starts with the exact value of `GA_CALENDAR_TITLE_PREFIX`
- confirm the description contains both `Company Name` and `Company Website`
- confirm the event is inside the configured lookback/lookahead window
- confirm `GA_GOOGLE_CALENDAR_ID` identifies the correct Calendar

### Google OAuth returns an access or refresh error

Confirm the user is allowed by the OAuth consent screen, then regenerate the token using `calendar-auth --force`.

### ngrok shows `502 Bad Gateway`

The tunnel cannot reach the local application. Confirm the server is running on `127.0.0.1:8787` and that ngrok forwards to port 8787.

### Fathom returns `401`

Confirm the webhook signing secret—not the API key—is stored in `GA_FATHOM_WEBHOOK_SECRET`, then restart the server. Do not disable signature verification for a public tunnel.

### Fathom keeps retrying

Inspect the request at `http://127.0.0.1:4040`, review the server log, and check the webhook attempt/activity section in the dashboard. A successful accepted delivery returns `202`.

### Semrush reports unavailable or unknown

Run `semrush-check`. The configured account may not have the required API plan, database, report entitlement, or remaining units. Leave Semrush disabled to continue with public evidence.

### Notion returns `401`, `403`, or `404`

- `401`: the integration secret is invalid or expired
- `403`: the integration lacks the required content capability
- `404`: the parent page ID is wrong or the page has not been shared with the integration

After correcting `.env` or Notion access, restart the server and retry publishing.

### AI generation fails

Confirm `GA_AI_BASE_URL`, `GA_AI_API_KEY`, and `GA_AI_MODEL`, then verify that the configured provider/model supports the OpenAI-compatible Chat Completions request used by this application. Also check account credit, rate limits, and the server log.

## Security and production notes

- Local development has no login and must remain bound to `127.0.0.1`.
- Production enables signed operator login and refuses startup when its username,
  password, or session-signing secret is missing.
- The ngrok policy is suitable for local Fathom testing because it exposes only the webhook route.
- The EC2 deployment binds the application to loopback behind Nginx. Only `/health`,
  `/login`, and the signature-verified Fathom webhook work without an operator
  session; dashboard, documents, admin and internal APIs require login.
- Keep `.env`, Google OAuth files, API keys, webhook secrets, transcripts, and generated client documents out of Git.
- Back up the `data/` directory because it contains the SQLite workflow database and generated artifacts.
- SQLite is appropriate for a single service instance. Coordinate database/storage changes before running multiple replicas.
- Licensed or private metrics are never inferred from public data. Account-level ROAS, CPA, spend, Search Console, and similar data are reported only when genuine authorized evidence is available.
- Nothing is publicly released without the required human/founder approval.

## Additional documentation

- [Phase 1 Calendar and pre-call setup](docs/phase-1-calendar-precall.md)
- [Production runbook](docs/production-runbook.md)
- [Workflow specification](docs/workflow-spec.md)
- [Calendar event template](config/calendar-event-template.md)

## Current product boundary

The service currently completes Calendar booking resolution, pre-call research, polished pre-call reporting, Fathom transcript ingestion, direct AI document generation, human approval, and private Notion package creation.

Final Gamma export, founder-email consent handling, public Notion publishing, LinkedIn queuing, and two-founder newsletter batching are later workflow slices.
