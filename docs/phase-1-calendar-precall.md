# Phase 1: Calendar to pre-call intelligence

Phase 1 ends when a qualifying Google Calendar event reliably produces a saved
pre-call evidence pack and a directly generated report containing exactly 10
positives, 10 gaps, and 5 discovery questions. Fathom, post-call documents,
approval, and Notion publishing are deliberately outside this activation step.

## Runtime boundary

- Growth Autopsy polls Google Calendar, validates events, schedules work, gathers
  bounded public evidence, stores SQL state, and persists the evidence files.
- A direct OpenAI-compatible model endpoint synthesizes the saved evidence.
- Semrush MCP is an optional, read-only application integration. The public Playwright,
  search, PageSpeed, and Lighthouse collectors remain the baseline.
- The model never owns the Calendar polling loop, workflow state, or timing.

## 1. Download a new Google OAuth client JSON

Use the same Google Cloud project in which the Calendar API is enabled.

1. Open Google Cloud Console, then **Google Auth Platform → Clients**.
2. If the existing OAuth client is a **Desktop app**, open it and download its
   JSON. If it is missing or you intentionally want a new client secret, choose
   **Create client → Desktop app**, name it `Growth Autopsy local controller`,
   create it, and download the JSON.
3. If the OAuth app is in Testing mode, ensure the Google account whose Calendar
   will be read is included as a test user.
4. Leave the downloaded file outside the repository. Do not paste its contents
   into `.env` and do not commit it.

The downloaded JSON identifies the OAuth application. The authorization command
below exchanges it for a separate Calendar-read-only authorized-user token.

## 2. Install and create the local environment

From the Growth Autopsy repository:

```bash
cd "/Users/jai/Jai Files/Automation/Growth-Autopsy"
cp .env.example .env
uv sync --extra dev
uv run playwright install chromium
npm install
uv run growth-autopsy init-db
```

The example environment already contains:

```dotenv
GA_GOOGLE_TOKEN_FILE=./secrets/google-token.json
GA_GOOGLE_CALENDAR_ID=primary
```

## 3. Authorize Calendar read-only access

Replace the example filename with the actual downloaded file:

```bash
uv run growth-autopsy calendar-auth \
  --client-secret "/Users/jai/Downloads/client_secret_XXXXXXXX.apps.googleusercontent.com.json"
```

Complete the Google consent screen in the browser. The command requests only
`https://www.googleapis.com/auth/calendar.readonly`, writes the resulting token
to the path configured by `GA_GOOGLE_TOKEN_FILE`, and restricts the local token
file permissions. The token content is never printed.

The secret JSON does not need to be loaded into an environment variable. The
command safely turns it into `./secrets/google-token.json`, while `.env` stores
only that file path.

Verify access without changing workflow state:

```bash
uv run growth-autopsy calendar-check
```

## 4. Create one test discovery call

Use the exact description contract in
[`config/calendar-event-template.md`](../config/calendar-event-template.md).
Keep the configured title prefix and provide these required fields:

```text
Title: [GROWTH AUTOPSY] Example Company – Founder Name

Company Name: Example Company
Company Website: https://example.com
```

`Founder Email`, `Founder LinkedIn`, and `Meeting Agenda` are optional but strongly
recommended because they improve the brief and the discovery questions. The
attendee list can supply the founder email and the founder name can be inferred
from the title. Legacy `Company`, `Website`, and `Agenda` labels remain accepted.
A company and public website are required. Missing required data moves the event
to `NEEDS_INPUT` and prevents research from running.

## 5. Configure the direct AI model

In Growth Autopsy's `.env`, configure an OpenAI-compatible Chat Completions
endpoint:

```dotenv
GA_AI_BASE_URL=https://api.openai.com/v1
GA_AI_API_KEY=your-provider-key
GA_AI_MODEL=your-model-name
```

The model is not an automation agent. It receives one evidence corpus and
returns one validated report source. The dashboard presents the report as a
polished HTML business document and provides a print-ready PDF download; raw
Markdown is not exposed to the marketing operator. Calendar access, scraping,
Semrush calls, timing, SQL state, files, and retries remain ordinary application
code.

## 6. Optional Semrush MCP

Semrush MCP is not an unrestricted free data source. It requires an eligible
Semrush subscription, consumes API units, and Trends traffic data additionally
depends on a Trends API plan. Keep this disabled while proving Calendar and the
free public-data baseline:

```dotenv
GA_SEMRUSH_MCP_ENABLED=false
```

If the account has the required access, use its Semrush API key for the direct
MCP connection:

```dotenv
GA_SEMRUSH_API_KEY=your-eligible-semrush-api-key
GA_SEMRUSH_MCP_ENABLED=true
GA_SEMRUSH_MCP_URL=https://mcp.semrush.com/v2/mcp
GA_SEMRUSH_MCP_MAX_REPORTS=3
```

Verify authentication and tool discovery without running paid reports:

```bash
uv run growth-autopsy semrush-check
```

The application permits only domain overview, organic research, and traffic
overview discovery plus schema lookup and report execution. It caps execution,
labels Semrush values as third-party estimates, and never infers private ROAS,
CPA, spend, or conversion data.

If Semrush is unavailable, the pipeline continues with public sources and marks
traffic/keyword metrics unavailable instead of inventing them. `GA_SEMRUSH_API_KEY`
is used only as the official MCP authentication key in this phase.

## 7. Activate and verify Phase 1

With the direct AI settings configured, perform one explicit sync:

```bash
cd "/Users/jai/Jai Files/Automation/Growth-Autopsy"
uv run growth-autopsy calendar-sync
uv run growth-autopsy status
uv run growth-autopsy serve --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`. For the test event, verify this sequence:

1. Calendar event is detected and stored once by its Google event ID.
2. Evidence collection is scheduled for T-60, or immediately if already inside
   that window.
3. Public evidence JSON and Markdown are saved before synthesis.
4. The configured direct AI endpoint returns the report for the T-30 deadline.
5. The meeting card moves through its live pipeline and shows **View analysis**
   plus **Download PDF** when the report is ready.
6. Editing, rescheduling, or cancelling the test event updates the same workflow
   rather than creating a duplicate.

Only after this test passes should `GA_ENABLE_BACKGROUND_SYNC=true` be left on
for the continuous 60-second Calendar poller.
