const state = {
  data: null,
  detail: null,
  query: "",
  status: "all",
  layout: "board",
  selected: null,
  running: new Set(),
  busy: false,
  revisionArtifact: null,
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
const titleCase = (value = "") => String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const strategyLabel = (value = "") => value === "case_study_only" ? "Growth Report Only" : titleCase(value || "Auto");
const formatDate = (value) => value ? new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "Date pending";
const formatShortDate = (value) => value ? new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(value)) : "—";
const formatTime = (value) => value ? new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "—";

const FULL_PIPELINE = [
  { key: "booking", label: "Booked", description: "Calendar meeting" },
  { key: "precall", label: "Pre-call", description: "Website research" },
  { key: "call", label: "Discovery call", description: "Founder conversation" },
  { key: "transcript", label: "Transcript", description: "Fathom capture" },
  { key: "intelligence", label: "AI analysis", description: "Founder intelligence" },
  { key: "growth_report", label: "Growth report", description: "Growth Intelligence Report" },
  { key: "strategy", label: "Strategy + share assets", description: "One-problem route" },
  { key: "approval", label: "Approval", description: "Review and edit" },
  { key: "publish", label: "Publish", description: "Notion + LinkedIn" },
];

const BOARD_COLUMNS = [
  ...FULL_PIPELINE,
  { key: "cancelled", label: "Cancelled", description: "Stopped meetings" },
];

const DISPLAY_DOCUMENTS = new Set([
  "precall_research",
  "founder_intelligence",
  "growth_autopsy",
  "linkedin_post",
  "strategy_doc",
  "pitch_deck_brief",
  "linkedin_publication",
]);

function requestHeaders(json = false) {
  return json ? { "Content-Type": "application/json" } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...requestHeaders(Boolean(options.body)), ...(options.headers || {}) },
  });
  if (response.status === 401) {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response;
}

function notify(message, error = false) {
  const node = $("notice");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.remove("hidden");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => node.classList.add("hidden"), 6500);
}

function isAnalyzing(item) {
  return state.running.has(item.calendar_event_id)
    || item.status === "ANALYSIS_RUNNING"
    || item.artifacts.some((artifact) => artifact.status === "PROCESSING");
}

function documentsFor(item) {
  return item.artifacts.filter((artifact) => DISPLAY_DOCUMENTS.has(artifact.kind));
}

function primaryDocument(item) {
  const documents = documentsFor(item).filter((artifact) => artifact.has_file);
  return documents.find((artifact) => artifact.kind === "precall_research") || documents[0] || null;
}

function requiresAttention(item) {
  return ["NEEDS_INPUT", "FAILED"].includes(item.status)
    || item.precall_delivery_state === "overdue"
    || item.artifacts.some((artifact) => artifact.status === "FAILED");
}

function boardStage(item) {
  if (item.status === "CANCELLED") return "cancelled";
  return item.current_stage?.key || "booking";
}

function statusMeta(item) {
  if (isAnalyzing(item)) return { label: "Analyzing", tone: "working" };
  if (item.status === "CANCELLED") return { label: "Cancelled", tone: "neutral" };
  if (requiresAttention(item)) return { label: "Needs attention", tone: "danger" };
  if (item.status === "PUBLISHED") return { label: "Published", tone: "success" };
  if (item.approval.awaiting_review > 0) return { label: "Review needed", tone: "warning" };
  if (documentsFor(item).some((artifact) => artifact.has_file)) return { label: "Report ready", tone: "success" };
  if (item.status === "RESEARCH_SCHEDULED") return { label: "Scheduled", tone: "neutral" };
  return { label: titleCase(item.status), tone: "neutral" };
}

function progressSegments(item, labelled = false) {
  return `<div class="progress-segments ${labelled ? "labelled" : ""}" aria-label="${item.progress}% complete">
    ${item.stages.map((stage) => `<span class="${stage.state}" title="${escapeHtml(stage.label)}"><i>${stage.state === "complete" ? "✓" : ""}</i>${labelled ? `<b>${escapeHtml(stage.label)}</b>` : ""}</span>`).join("")}
  </div>`;
}

function analyzingStrip(item, compact = false) {
  if (!isAnalyzing(item)) return "";
  return `<div class="analyzing-strip ${compact ? "compact" : ""}" role="status">
    <span class="spinner"><i></i><i></i><i></i></span>
    <span><strong>Analyzing marketing signals</strong>${compact ? "" : "<small>Website, search, performance, channels and growth gaps</small>"}</span>
    <em></em>
  </div>`;
}

function filteredMeetings() {
  const query = state.query.trim().toLowerCase();
  return (state.data?.appointments || [])
    .filter((item) => {
      const search = [item.title, item.company, item.founder_name, item.founder_email, item.industry, item.next_action].join(" ").toLowerCase();
      if (query && !search.includes(query)) return false;
      if (state.status === "active" && ["PUBLISHED", "CANCELLED"].includes(item.status)) return false;
      if (state.status === "approval" && item.approval.awaiting_review === 0 && !item.artifacts.some((artifact) => artifact.action_required === "route")) return false;
      if (state.status === "attention" && !requiresAttention(item)) return false;
      if (state.status === "complete" && item.status !== "PUBLISHED") return false;
      if (state.status === "cancelled" && item.status !== "CANCELLED") return false;
      return true;
    })
    .sort((a, b) => Number(isAnalyzing(b)) - Number(isAnalyzing(a)) || new Date(a.start_at) - new Date(b.start_at));
}

function cardStatus(item) {
  const status = statusMeta(item);
  return `<span class="status-pill ${status.tone}"><i></i>${escapeHtml(status.label)}</span>`;
}

function pipelineCard(item) {
  const document = primaryDocument(item);
  return `<article class="pipeline-card ${isAnalyzing(item) ? "is-analyzing" : ""}" role="button" tabindex="0" data-open="${escapeHtml(item.calendar_event_id)}">
    <header><span class="card-date"><strong>${escapeHtml(formatShortDate(item.start_at))}</strong><small>${escapeHtml(formatTime(item.start_at))}</small></span>${cardStatus(item)}</header>
    <h3>${escapeHtml(item.title || item.company || "Untitled meeting")}</h3>
    <p>${escapeHtml(item.company || "Company pending")}${item.founder_name ? ` · ${escapeHtml(item.founder_name)}` : ""}</p>
    <div class="card-progress"><span><strong>${escapeHtml(item.current_stage?.label || "Booked")}</strong><b>${item.progress}%</b></span><div><i style="width:${item.progress}%"></i></div></div>
    ${progressSegments(item)}
    ${analyzingStrip(item, true)}
    <div class="card-next"><span>Next</span><strong>${escapeHtml(item.next_action)}</strong></div>
    <footer><button type="button" data-open="${escapeHtml(item.calendar_event_id)}">Open details</button>${document ? `<button class="report-link" type="button" data-view-document="${document.id}">View report</button>` : ""}</footer>
  </article>`;
}

function renderBoard(meetings) {
  return `<div class="board-shell"><div class="pipeline-board">
    ${BOARD_COLUMNS.map((column, index) => {
      const items = meetings.filter((item) => boardStage(item) === column.key);
      const hideCancelled = column.key === "cancelled" && items.length === 0 && state.status !== "cancelled";
      if (hideCancelled) return "";
      return `<section class="board-column tone-${index % 6}">
        <header class="column-head"><span><i>${String(index + 1).padStart(2, "0")}</i><strong>${escapeHtml(column.label)}</strong><small>${escapeHtml(column.description)}</small></span><b>${items.length}</b></header>
        <div class="column-cards">${items.length ? items.map(pipelineCard).join("") : `<div class="column-empty"><span>○</span><p>No meetings here</p></div>`}</div>
      </section>`;
    }).join("")}
  </div></div>`;
}

function renderTable(meetings) {
  if (!meetings.length) return emptyState();
  return `<div class="table-shell"><table class="pipeline-table">
    <thead><tr><th>Meeting</th><th>Scheduled</th><th>Current stage</th><th>Progress</th><th>Next action</th><th>Report</th><th>Status</th></tr></thead>
    <tbody>${meetings.map((item) => {
      const document = primaryDocument(item);
      return `<tr data-open="${escapeHtml(item.calendar_event_id)}">
        <td><strong>${escapeHtml(item.title || item.company || "Untitled meeting")}</strong><small>${escapeHtml(item.company || "Company pending")}${item.founder_name ? ` · ${escapeHtml(item.founder_name)}` : ""}</small>${isAnalyzing(item) ? analyzingStrip(item, true) : ""}</td>
        <td><strong>${escapeHtml(formatShortDate(item.start_at))}</strong><small>${escapeHtml(formatTime(item.start_at))}</small></td>
        <td><span class="stage-chip">${escapeHtml(item.current_stage?.label || "Booked")}</span></td>
        <td><div class="table-progress"><span><i style="width:${item.progress}%"></i></span><b>${item.progress}%</b></div></td>
        <td class="next-cell">${escapeHtml(item.next_action)}</td>
        <td>${document ? `<div class="table-actions"><button data-view-document="${document.id}">View</button><button data-download="${document.id}">PDF</button></div>` : "<span class=\"muted\">Pending</span>"}</td>
        <td>${cardStatus(item)}</td>
      </tr>`;
    }).join("")}</tbody>
  </table></div>`;
}

function emptyState() {
  return `<div class="empty-state"><div>○</div><h2>No meetings found</h2><p>Sync Calendar or change the current filter.</p></div>`;
}

function renderSummary() {
  const meetings = state.data?.appointments || [];
  const active = meetings.filter((item) => !["PUBLISHED", "CANCELLED"].includes(item.status)).length;
  const analyzing = meetings.filter(isAnalyzing).length;
  const approvals = meetings.reduce((sum, item) => sum + item.approval.awaiting_review, 0);
  $("summary").innerHTML = `
    <div><strong>${meetings.length}</strong><span>Total meetings</span></div>
    <div><strong>${active}</strong><span>In progress</span></div>
    <div class="${analyzing ? "live" : ""}"><strong>${analyzing}</strong><span>Analyzing</span></div>
    <div class="${approvals ? "attention" : ""}"><strong>${approvals}</strong><span>Awaiting approval</span></div>`;
}

function render() {
  if (!state.data) return;
  renderSummary();
  const meetings = filteredMeetings();
  $("viewRoot").className = state.layout === "board" ? "board-view" : "table-view";
  $("viewRoot").innerHTML = meetings.length ? (state.layout === "board" ? renderBoard(meetings) : renderTable(meetings)) : emptyState();
  $("boardLayout").classList.toggle("active", state.layout === "board");
  $("tableLayout").classList.toggle("active", state.layout === "table");
  $("boardHint").classList.toggle("hidden", state.layout !== "board");
  bindActions($("viewRoot"));
}

function renderSystemState() {
  const integrations = state.data?.system?.integrations || [];
  const critical = integrations.filter((integration) => ["calendar", "ai"].includes(integration.key));
  const attention = Boolean(state.data?.system?.calendar_last_error) || critical.some((integration) => ["attention", "not_configured"].includes(integration.state));
  $("systemState").className = `system-state ${attention ? "attention" : "online"}`;
  $("systemState").innerHTML = `<i></i><span>${attention ? "Setup needed" : "Automation live"}</span>`;
}

async function load(silent = false) {
  try {
    const response = await api("/internal/dashboard");
    state.data = await response.json();
    renderSystemState();
    render();
  } catch (error) {
    if (!silent) notify(error.message, true);
    $("systemState").className = "system-state attention";
    $("systemState").innerHTML = "<i></i><span>Offline</span>";
    if (!state.data) $("viewRoot").innerHTML = `<div class="empty-state"><div>!</div><h2>Dashboard unavailable</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function syncController() {
  if (state.busy) return;
  state.busy = true;
  $("syncButton").disabled = true;
  $("syncButton").classList.add("loading");
  try {
    await api("/internal/calendar/sync", { method: "POST" });
    await load(true);
    notify("Calendar synced and workflow states refreshed.");
  } catch (error) { notify(error.message, true); }
  finally {
    state.busy = false;
    $("syncButton").disabled = false;
    $("syncButton").classList.remove("loading");
  }
}

async function openDrawer(eventId) {
  state.selected = eventId;
  state.detail = null;
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("scrim").classList.remove("hidden");
  $("drawerContent").innerHTML = `<div class="page-loader"><i></i><span>Loading workflow…</span></div>`;
  try {
    const response = await api(`/internal/appointments/${encodeURIComponent(eventId)}`);
    state.detail = await response.json();
    renderDrawer();
  } catch (error) {
    $("drawerContent").innerHTML = `<div class="empty-state"><div>!</div><h2>Could not load meeting</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function openDeleteDialog() {
  if (!state.detail) return;
  $("deleteMeetingName").textContent = state.detail.title || state.detail.company || "This meeting";
  $("deleteDialog").showModal();
}

async function deleteMeeting() {
  const eventId = state.selected;
  if (!eventId || state.busy) return;
  state.busy = true;
  $("deleteSubmit").disabled = true;
  try {
    await api(`/internal/appointments/${encodeURIComponent(eventId)}`, { method: "DELETE" });
    state.running.delete(eventId);
    if (state.data) {
      state.data.appointments = state.data.appointments.filter((item) => item.calendar_event_id !== eventId);
    }
    $("deleteDialog").close();
    closeDrawer();
    render();
    notify("Meeting deleted from the Growth Autopsy dashboard.");
    await load(true);
  } catch (error) {
    notify(error.message, true);
  } finally {
    state.busy = false;
    $("deleteSubmit").disabled = false;
  }
}

function closeDrawer() {
  state.selected = null;
  state.detail = null;
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("scrim").classList.add("hidden");
}

function drawerDocument(artifact, eventId) {
  const review = artifact.action_required === "review";
  const retry = artifact.action_required === "retry";
  const publishRetry = artifact.action_required === "publish_retry";
  const verifyLinkedIn = artifact.action_required === "verify_linkedin";
  const fileActions = `${artifact.has_file ? `<button data-view-document="${artifact.id}">View</button><button data-download="${artifact.id}">PDF</button>` : ""}${artifact.external_url ? `<button data-url="${escapeHtml(artifact.external_url)}">Open ↗</button>` : ""}`;
  return `<article class="drawer-document">
    <span class="document-icon">▤</span>
    <span class="document-name"><strong>${escapeHtml(artifact.label)}</strong><small>${escapeHtml(titleCase(artifact.status))}${artifact.notes ? ` · ${escapeHtml(artifact.notes)}` : ""}</small></span>
    <span class="document-actions">${fileActions}${review ? `<button class="approve" data-approve="${artifact.id}">Approve</button><button data-revise="${artifact.id}" data-title="${escapeHtml(artifact.label)}">Revise</button>` : ""}${retry ? `<button data-retry="${artifact.id}">Retry</button>` : ""}${publishRetry ? `<button data-publish="${escapeHtml(eventId)}">Retry publish</button>` : ""}${verifyLinkedIn ? `<button data-linkedin-found="${escapeHtml(eventId)}">Record post</button><button data-linkedin-retry="${escapeHtml(eventId)}">No post — retry</button>` : ""}</span>
  </article>`;
}

function renderDrawer() {
  const item = state.detail;
  if (!item) return;
  const status = statusMeta(item);
  const documents = documentsFor(item);
  const routing = item.artifacts.find((artifact) => artifact.action_required === "route");
  const events = item.timeline.slice(0, 8);
  const allApproved = item.approval.required > 0 && item.approval.approved === item.approval.required;
  $("drawerContent").innerHTML = `<div class="drawer-body">
    <header class="detail-header"><span class="eyebrow">${escapeHtml(item.current_stage.label)}</span><h2>${escapeHtml(item.title || item.company)}</h2><p>${escapeHtml(item.company)}${item.founder_name ? ` · ${escapeHtml(item.founder_name)}` : ""} · ${escapeHtml(formatDate(item.start_at))}</p><span class="status-pill ${status.tone}"><i></i>${escapeHtml(status.label)}</span></header>
    ${analyzingStrip(item)}
    <section class="next-action"><span>Next best action</span><strong>${escapeHtml(item.next_action)}</strong></section>
    ${item.meeting_agenda ? `<section class="meeting-agenda"><span>Meeting agenda</span><p>${escapeHtml(item.meeting_agenda).replaceAll("\n", "<br>")}</p></section>` : ""}
    <div class="detail-actions">
      ${item.website ? `<button class="button secondary small" data-url="${escapeHtml(item.website)}">Website ↗</button>` : ""}
      ${item.conference_url ? `<button class="button secondary small" data-url="${escapeHtml(item.conference_url)}">Google Meet ↗</button>` : ""}
      ${item.precall_can_run && !isAnalyzing(item) ? `<button class="button small" data-run-precall="${escapeHtml(item.calendar_event_id)}">${documents.some((artifact) => artifact.kind === "precall_research") ? "Refresh research" : "Run research"}</button>` : ""}
      ${allApproved ? `<button class="button small" data-publish="${escapeHtml(item.calendar_event_id)}">Publish approved package</button>` : ""}
    </div>
    <section class="detail-section"><header><h3>Complete pipeline</h3><span>${item.progress}% complete</span></header>${progressSegments(item, true)}</section>
    <section class="detail-section"><header><h3>Meeting snapshot</h3></header><div class="snapshot-grid"><div><span>Founder</span><strong>${escapeHtml(item.founder_name || item.founder_email || "Pending")}</strong></div><div><span>Industry</span><strong>${escapeHtml(item.industry || "Pending")}</strong></div><div><span>Pre-call report</span><strong>${escapeHtml(titleCase(item.precall_delivery_state))}</strong></div><div><span>Strategy</span><strong>${escapeHtml(strategyLabel(item.strategy_intent))}</strong></div><div><span>Service lane</span><strong>${escapeHtml(titleCase(item.service_lane || "Unsure"))}</strong></div></div></section>
    <section class="detail-section"><header><h3>Documents</h3><span>${documents.length} artifacts</span></header><div class="drawer-documents">${documents.length ? documents.map((artifact) => drawerDocument(artifact, item.calendar_event_id)).join("") : `<div class="document-empty">Documents appear here as the workflow moves forward.</div>`}</div></section>
    ${routing ? `<section class="strategy-choice"><span><strong>Strategy decision</strong><small>Choose the right output for this founder conversation.</small></span><div><button class="button small" data-strategy="strategy_requested" data-event="${escapeHtml(item.calendar_event_id)}">Create strategy + deck</button><button class="button secondary small" data-strategy="case_study_only" data-event="${escapeHtml(item.calendar_event_id)}">Growth report only</button></div></section>` : ""}
    <details class="activity-details"><summary>Recent workflow activity</summary>${events.length ? events.map((event) => `<p><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.detail || "")}</span></p>`).join("") : "<p>No activity yet.</p>"}</details>
  </div>`;
  bindActions($("drawerContent"));
}

async function runPrecall(eventId) {
  if (state.running.has(eventId)) return;
  state.running.add(eventId);
  render();
  if (state.detail?.calendar_event_id === eventId) renderDrawer();
  try {
    const response = await api(`/internal/appointments/${encodeURIComponent(eventId)}/precall/run`, { method: "POST" });
    const result = await response.json();
    notify(result.status === "ready" ? "Pre-call analysis is ready." : `Research ${titleCase(result.status)}.`);
  } catch (error) { notify(error.message, true); }
  finally {
    state.running.delete(eventId);
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  }
}

async function viewArtifact(artifactId) {
  const tab = window.open("", "_blank");
  if (tab) tab.document.write("<title>Opening report…</title><p style='font-family:system-ui;padding:32px'>Opening report…</p>");
  try {
    const response = await api(`/internal/artifacts/${artifactId}/view`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (tab) tab.location.replace(url); else window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    if (tab) tab.close();
    notify(error.message, true);
  }
}

async function downloadArtifact(artifactId) {
  try {
    const response = await api(`/internal/artifacts/${artifactId}/download`);
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    const filename = encoded ? decodeURIComponent(encoded) : plain || "growth-autopsy-report.pdf";
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { notify(error.message, true); }
}

async function approveArtifact(artifactId) {
  try {
    const response = await api(`/internal/artifacts/${artifactId}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve", notes: "" }) });
    const result = await response.json();
    if (result.dependent_generation?.status === "failed") {
      notify(`Document approved, but the dependent draft failed: ${result.dependent_generation.error}`, true);
    } else if (result.dependent_generation?.status === "ready") {
      notify("Document approved. Its dependent draft is ready for review.");
    } else {
      notify("Document approved.");
    }
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

function openRevisionDialog(artifactId, title) {
  state.revisionArtifact = artifactId;
  $("revisionNotes").value = "";
  $("revisionDialog").querySelector("h2").textContent = `Revise ${title || "document"}`;
  $("revisionDialog").showModal();
}

async function submitRevision(notes) {
  try {
    await api(`/internal/artifacts/${state.revisionArtifact}/decision`, { method: "POST", body: JSON.stringify({ decision: "revise", notes }) });
    $("revisionDialog").close();
    state.revisionArtifact = null;
    notify("Revision started.");
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

async function retryArtifact(artifactId) {
  try {
    const response = await api(`/internal/artifacts/${artifactId}/retry`, { method: "POST" });
    const result = await response.json();
    notify(result.status === "ready" ? "Document regenerated and ready for review." : `Retry ${titleCase(result.status)}.`);
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

async function decideStrategy(eventId, intent) {
  try {
    await api(`/internal/appointments/${encodeURIComponent(eventId)}/strategy-decision`, { method: "POST", body: JSON.stringify({ intent }) });
    notify(intent === "strategy_requested" ? "Strategy generation started; the deck follows approval." : "Growth-report-only route selected.");
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

async function publishNotion(eventId) {
  try {
    const response = await api(`/internal/appointments/${encodeURIComponent(eventId)}/notion/publish`, { method: "POST" });
    const result = await response.json();
    if (result.status === "linkedin_verification_required") {
      notify(result.linkedin?.error || "Check the LinkedIn profile before retrying.", true);
    } else if (result.status === "linkedin_authorization_required") {
      notify("Reconnect LinkedIn from Admin → Configuration, then retry publishing.", true);
    } else {
      notify(["published", "already_published"].includes(result.status) ? "Approved package published." : `Publishing: ${titleCase(result.status)}.`);
    }
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

async function resolveLinkedIn(eventId, outcome, postUrl = "") {
  try {
    const response = await api(`/internal/appointments/${encodeURIComponent(eventId)}/linkedin/resolve`, {
      method: "POST",
      body: JSON.stringify({ outcome, post_url: postUrl }),
    });
    const result = await response.json();
    notify(result.status === "recorded" ? "Existing LinkedIn post recorded." : "LinkedIn retry completed.");
    await load(true);
    if (state.selected) await openDrawer(state.selected);
  } catch (error) { notify(error.message, true); }
}

function recordUncertainLinkedInPost(eventId) {
  const postUrl = window.prompt("Paste the LinkedIn post URL you found on the connected profile:");
  if (postUrl?.trim()) resolveLinkedIn(eventId, "published", postUrl.trim());
}

function retryUncertainLinkedInPost(eventId) {
  if (window.confirm("Confirm that you checked the connected LinkedIn profile and no post exists. Retry once?")) {
    resolveLinkedIn(eventId, "retry");
  }
}

function openExternal(url) {
  try {
    const parsed = new URL(url);
    if (!parsed.protocol.startsWith("http")) throw new Error("Unsupported URL");
    window.open(parsed.href, "_blank", "noopener,noreferrer");
  } catch { notify("This link is invalid.", true); }
}

function bindActions(root) {
  root.querySelectorAll("[data-open]").forEach((node) => {
    node.addEventListener("click", (event) => { event.stopPropagation(); openDrawer(node.dataset.open); });
    node.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); openDrawer(node.dataset.open); } });
  });
  root.querySelectorAll("[data-view-document]").forEach((node) => node.addEventListener("click", (event) => { event.stopPropagation(); viewArtifact(node.dataset.viewDocument); }));
  root.querySelectorAll("[data-download]").forEach((node) => node.addEventListener("click", (event) => { event.stopPropagation(); downloadArtifact(node.dataset.download); }));
  root.querySelectorAll("[data-run-precall]").forEach((node) => node.addEventListener("click", () => runPrecall(node.dataset.runPrecall)));
  root.querySelectorAll("[data-approve]").forEach((node) => node.addEventListener("click", () => approveArtifact(node.dataset.approve)));
  root.querySelectorAll("[data-revise]").forEach((node) => node.addEventListener("click", () => openRevisionDialog(node.dataset.revise, node.dataset.title)));
  root.querySelectorAll("[data-retry]").forEach((node) => node.addEventListener("click", () => retryArtifact(node.dataset.retry)));
  root.querySelectorAll("[data-strategy]").forEach((node) => node.addEventListener("click", () => decideStrategy(node.dataset.event, node.dataset.strategy)));
  root.querySelectorAll("[data-publish]").forEach((node) => node.addEventListener("click", () => publishNotion(node.dataset.publish)));
  root.querySelectorAll("[data-linkedin-found]").forEach((node) => node.addEventListener("click", () => recordUncertainLinkedInPost(node.dataset.linkedinFound)));
  root.querySelectorAll("[data-linkedin-retry]").forEach((node) => node.addEventListener("click", () => retryUncertainLinkedInPost(node.dataset.linkedinRetry)));
  root.querySelectorAll("[data-url]").forEach((node) => node.addEventListener("click", () => openExternal(node.dataset.url)));
}

$("syncButton").addEventListener("click", syncController);
$("globalSearch").addEventListener("input", (event) => { state.query = event.target.value; render(); });
$("statusFilter").addEventListener("change", (event) => { state.status = event.target.value; render(); });
document.querySelectorAll("[data-layout]").forEach((node) => node.addEventListener("click", () => { state.layout = node.dataset.layout; render(); }));
$("closeDrawer").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
$("deleteMeetingButton").addEventListener("click", openDeleteDialog);
$("deleteForm").addEventListener("submit", (event) => {
  event.preventDefault();
  deleteMeeting();
});
$("revisionForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const notes = $("revisionNotes").value.trim();
  if (notes) submitRevision(notes);
});
document.querySelectorAll("[data-close-modal]").forEach((node) => node.addEventListener("click", () => $(node.dataset.closeModal).close()));
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.selected) closeDrawer(); });

load();
setInterval(() => load(true), 10000);
