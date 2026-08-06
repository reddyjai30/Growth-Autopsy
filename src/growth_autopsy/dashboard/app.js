const state = { data: null, selected: null, view: "overview", busy: false };
const $ = (id) => document.getElementById(id);
const key = () => sessionStorage.getItem("ga_internal_key") || "";
const headers = (json = false) => ({ ...(key() ? { "X-Internal-Key": key() } : {}), ...(json ? { "Content-Type": "application/json" } : {}) });
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const formatDate = (value, options = {}) => value ? new Intl.DateTimeFormat("en-IN", { dateStyle:"medium", timeStyle:"short", ...options }).format(new Date(value)) : "—";

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) } });
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const body = await response.json(); message = body.detail || message; } catch {} throw new Error(message); }
  return response;
}

function notify(message, error = false) { const node = $("notice"); node.textContent = message; node.classList.remove("hidden"); node.style.borderColor = error ? "#e6c5bd" : "#b9d6ca"; node.style.background = error ? "#fff0ec" : "#e8f4ef"; node.style.color = error ? "#a6473d" : "#17634f"; setTimeout(() => node.classList.add("hidden"), 7000); }

function filteredAppointments() {
  const rows = state.data?.appointments || [];
  const today = new Date().toDateString();
  if (state.view === "calls") return rows.filter(x => new Date(x.start_at).toDateString() === today);
  if (state.view === "approvals") return rows.filter(x => x.artifacts.some(a => ["READY","REVISION_REQUESTED"].includes(a.status)));
  return rows;
}

function render() {
  const data = state.data; if (!data) return;
  $("metricCalls").textContent = data.metrics.appointments;
  $("metricActive").textContent = data.metrics.active;
  $("metricAttention").textContent = data.metrics.needs_attention;
  $("navCalls").textContent = data.appointments.filter(x => new Date(x.start_at).toDateString() === new Date().toDateString()).length;
  $("navApprovals").textContent = data.appointments.filter(x => x.artifacts.some(a => a.status === "READY")).length;
  const last = data.system.calendar_last_sync_at ? `Calendar checked ${formatDate(data.system.calendar_last_sync_at)}` : "Waiting for first calendar sync";
  $("controllerDetail").textContent = data.system.calendar_last_error ? `Calendar error: ${data.system.calendar_last_error}` : last;
  $("systemState").textContent = data.system.calendar_last_error ? "Automation needs attention" : "Automation online";
  const rows = filteredAppointments();
  $("workflowList").innerHTML = rows.length ? rows.map(workflowCard).join("") : `<div class="empty">No workflows match this view.</div>`;
  document.querySelectorAll(".workflow").forEach(node => node.addEventListener("click", () => openDrawer(node.dataset.id)));
  if (state.selected) { const current = data.appointments.find(x => x.calendar_event_id === state.selected); if (current) renderDrawer(current); }
}

function workflowCard(item) {
  return `<article class="workflow" data-id="${escapeHtml(item.calendar_event_id)}">
    <div class="workflow-head"><div class="avatar">${escapeHtml((item.company || "?")[0].toUpperCase())}</div><div class="workflow-title"><strong>${escapeHtml(item.company)}</strong><span>${escapeHtml(item.founder_name || "Founder not specified")} · ${escapeHtml(item.industry || "Industry pending")}</span></div><div class="workflow-time">${formatDate(item.start_at)}<span>${escapeHtml(item.status.replaceAll("_", " "))}</span></div></div>
    <div class="track">${item.stages.map(stage => `<div class="track-step ${stage.state}"><i></i><span>${escapeHtml(stage.label)}</span></div>`).join("")}</div>
  </article>`;
}

function renderDrawer(item) {
  const artifacts = item.artifacts.length ? item.artifacts.map(artifactCard).join("") : `<div class="empty">No files yet. Evidence and reports appear here as each stage completes.</div>`;
  $("drawerContent").innerHTML = `<span class="eyebrow">Founder workflow</span><h2>${escapeHtml(item.company)} × ${escapeHtml(item.founder_name || "Founder")}</h2><div class="drawer-meta"><span>${formatDate(item.start_at)}</span><span>${escapeHtml(item.industry || "Industry pending")}</span><span>${escapeHtml(item.strategy_mode)}</span></div>
  ${item.last_error ? `<p class="error-text">${escapeHtml(item.last_error)}</p>` : ""}
  <div class="run-row"><button id="runNow" ${item.precall_can_run ? "" : "disabled"}>${item.artifacts.some(a => a.kind === "precall_research") ? "Re-run pre-call research" : "Run pre-call research now"}</button><button class="secondary" id="visitSite">Open website</button></div>
  <section class="drawer-section progress-wrap"><div class="progress-label"><span>Workflow progress</span><strong>${item.progress}%</strong></div><div class="progress"><i style="width:${item.progress}%"></i></div></section>
  <section class="drawer-section"><h3>Pipeline</h3><div class="stage-list">${item.stages.map(s => `<div class="stage ${s.state}"><span>${escapeHtml(s.label)}</span><span>${s.state}</span></div>`).join("")}</div></section>
  <section class="drawer-section"><h3>Reports & files · ${item.artifacts.length}</h3>${artifacts}</section>`;
  const run = $("runNow"); if (run) run.addEventListener("click", () => runPrecall(item));
  $("visitSite").addEventListener("click", () => window.open(item.website, "_blank", "noopener"));
  document.querySelectorAll("[data-download]").forEach(node => node.addEventListener("click", () => downloadArtifact(node.dataset.download)));
  document.querySelectorAll("[data-decision]").forEach(node => node.addEventListener("click", () => decideArtifact(node.dataset.id, node.dataset.decision)));
}

function artifactCard(item) {
  const actions = item.status === "READY" ? `<button data-decision="approve" data-id="${item.id}">Approve</button><button class="danger" data-decision="revise" data-id="${item.id}">Request revision</button>` : "";
  return `<article class="artifact"><div class="artifact-head"><div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.filename || item.kind)}${item.notes ? ` · ${escapeHtml(item.notes)}` : ""}</small></div><span class="status-pill status-${item.status}">${item.status.replaceAll("_", " ")}</span></div><div class="artifact-actions">${item.has_file ? `<button class="secondary" data-download="${item.id}">Download</button>` : ""}${actions}</div></article>`;
}

function openDrawer(id) { state.selected = id; const item = state.data.appointments.find(x => x.calendar_event_id === id); if (!item) return; renderDrawer(item); $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false"); $("scrim").classList.remove("hidden"); }
function closeDrawer() { state.selected = null; $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); $("scrim").classList.add("hidden"); }

async function load(silent = false) { try { const response = await api("/internal/dashboard"); state.data = await response.json(); render(); } catch (error) { if (!silent) notify(error.message, true); $("systemState").textContent = "Connection unavailable"; } }
async function sync() { if (state.busy) return; state.busy = true; $("syncButton").disabled = true; try { await api("/internal/calendar/sync", { method:"POST" }); notify("Calendar synced and due work checked."); await load(true); } catch (e) { notify(e.message, true); } finally { state.busy = false; $("syncButton").disabled = false; } }
async function runPrecall(item) { if (state.busy) return; state.busy = true; $("runNow").disabled = true; $("runNow").textContent = "Collecting public evidence…"; try { const response = await api(`/internal/appointments/${encodeURIComponent(item.calendar_event_id)}/precall/run`, { method:"POST" }); const result = await response.json(); notify(result.status === "synthesis_started" ? "Evidence saved. Gemini synthesis has started." : `Research ${result.status}.`, result.status === "failed"); await load(true); } catch(e) { notify(e.message, true); await load(true); } finally { state.busy = false; } }
async function downloadArtifact(id) { try { const response = await api(`/internal/artifacts/${id}/download`); const blob = await response.blob(); const disposition = response.headers.get("content-disposition") || ""; const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "growth-autopsy-report.md"; const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url); } catch(e) { notify(e.message, true); } }
async function decideArtifact(id, decision) { let notes = ""; if (decision === "revise") { notes = prompt("What should Gemini/Diksha change in this report?") || ""; if (!notes.trim()) return; } try { await api(`/internal/artifacts/${id}/decision`, { method:"POST", body:JSON.stringify({decision, notes}) }); notify(decision === "approve" ? "Report approved." : "Revision feedback saved."); await load(true); } catch(e) { notify(e.message, true); } }

document.querySelectorAll(".nav-item").forEach(node => node.addEventListener("click", () => { document.querySelectorAll(".nav-item").forEach(x => x.classList.remove("active")); node.classList.add("active"); state.view = node.dataset.view; render(); }));
$("syncButton").addEventListener("click", sync); $("closeDrawer").addEventListener("click", closeDrawer); $("scrim").addEventListener("click", closeDrawer);
$("keyButton").addEventListener("click", () => { $("keyInput").value = key(); $("keyDialog").showModal(); });
$("saveKey").addEventListener("click", () => { sessionStorage.setItem("ga_internal_key", $("keyInput").value.trim()); setTimeout(() => load(), 0); });
$("todayLabel").textContent = new Intl.DateTimeFormat("en-IN", { weekday:"long", day:"numeric", month:"long" }).format(new Date());
load(); setInterval(() => load(true), 15000);
