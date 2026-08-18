const byId = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const state = {
  overview: null,
  activeTable: "",
  table: null,
  limit: 25,
  offset: 0,
  search: "",
  config: null,
  dirty: new Set(),
  clearedSecrets: new Set(),
  restartRequired: false,
};

let noticeTimer;
let searchTimer;

function notify(message, isError = false) {
  const notice = byId("notice");
  window.clearTimeout(noticeTimer);
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.classList.remove("hidden");
  noticeTimer = window.setTimeout(() => notice.classList.add("hidden"), 5000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    const next = window.location.pathname + window.location.search + window.location.hash;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {
      // Keep the status-based message when the response is not JSON.
    }
    if (response.status === 403) {
      detail = "Open the admin console through http://127.0.0.1:8787 on this machine.";
    }
    throw new Error(detail);
  }
  return response.json();
}

function selectTab(name, updateLocation = true) {
  const configuration = name === "configuration";
  byId("databaseTab").classList.toggle("active", !configuration);
  byId("databaseTab").setAttribute("aria-selected", String(!configuration));
  byId("configurationTab").classList.toggle("active", configuration);
  byId("configurationTab").setAttribute("aria-selected", String(configuration));
  byId("databasePanel").classList.toggle("hidden", configuration);
  byId("configurationPanel").classList.toggle("hidden", !configuration);
  if (updateLocation) {
    window.history.replaceState(null, "", configuration ? "#configuration" : "#database");
  }
  if (configuration && !state.config) loadConfiguration();
  if (!configuration && !state.overview) loadDatabase();
}

function collectionMetadata(key) {
  return state.overview?.tables.find((table) => table.key === key) || null;
}

function renderCollections() {
  const list = byId("collectionList");
  list.innerHTML = state.overview.tables.map((table) => `
    <button class="collection-button ${table.key === state.activeTable ? "active" : ""}" type="button" data-table="${escapeHTML(table.key)}">
      <span class="collection-symbol" aria-hidden="true">${escapeHTML(table.label.slice(0, 2).toUpperCase())}</span>
      <span class="collection-copy"><strong>${escapeHTML(table.label)}</strong><small>${table.columns.length} fields</small></span>
      <span class="collection-count">${table.count.toLocaleString()}</span>
    </button>
  `).join("");
  list.querySelectorAll("[data-table]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTable = button.dataset.table;
      state.offset = 0;
      state.search = "";
      byId("databaseSearchInput").value = "";
      byId("clearDatabaseSearch").classList.add("hidden");
      renderCollections();
      loadRecords();
    });
  });
}

async function loadDatabase({ refreshRecords = true } = {}) {
  const refresh = byId("refreshDatabase");
  refresh.classList.add("loading");
  try {
    state.overview = await api("/internal/admin/database");
    byId("databaseEngine").textContent = state.overview.engine;
    byId("databasePath").textContent = state.overview.database_file;
    byId("databasePath").title = state.overview.database_file;
    if (!collectionMetadata(state.activeTable)) {
      state.activeTable = state.overview.tables[0]?.key || "";
    }
    renderCollections();
    if (refreshRecords && state.activeTable) await loadRecords();
  } catch (error) {
    notify(error.message, true);
    byId("collectionList").innerHTML = `<div class="sidebar-loader"><span>${escapeHTML(error.message)}</span></div>`;
  } finally {
    refresh.classList.remove("loading");
  }
}

function valuePreview(value) {
  if (value === null || value === undefined) return { text: "null", className: "null-value" };
  if (typeof value === "object") {
    return {
      text: `${value.preview || ""}${value.truncated ? `… (${value.characters} chars)` : ""}`,
      className: "structured-value",
    };
  }
  const text = String(value);
  const structured = (text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"));
  return { text, className: structured ? "structured-value" : "" };
}

function renderRecords() {
  const payload = state.table;
  const tableShell = byId("tableShell");
  const empty = byId("recordsEmpty");
  const pagination = byId("pagination");
  const columns = [{ name: "__rowid__", type: "ROWID" }, ...payload.columns];
  const metadata = collectionMetadata(payload.table);

  byId("collectionTitle").textContent = payload.label;
  byId("collectionDescription").textContent = metadata?.description || "Workflow database records";
  byId("recordSummary").textContent = `${payload.total.toLocaleString()} record${payload.total === 1 ? "" : "s"}`;
  byId("dataTableHead").innerHTML = `<tr>${columns.map((column) => `<th title="${escapeHTML(column.type || "")}">${escapeHTML(column.name)}</th>`).join("")}</tr>`;

  if (!payload.rows.length) {
    tableShell.classList.add("hidden");
    pagination.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");
  tableShell.classList.remove("hidden");
  pagination.classList.remove("hidden");
  byId("dataTableBody").innerHTML = payload.rows.map((row) => `
    <tr data-rowid="${escapeHTML(row.__rowid__)}" tabindex="0" aria-label="Inspect row ${escapeHTML(row.__rowid__)}">
      ${columns.map((column) => {
        const preview = valuePreview(row[column.name]);
        const rowIdClass = column.name === "__rowid__" ? "row-id" : "";
        return `<td><span class="${preview.className} ${rowIdClass}" title="${escapeHTML(preview.text)}">${escapeHTML(preview.text)}</span></td>`;
      }).join("")}
    </tr>
  `).join("");

  byId("dataTableBody").querySelectorAll("tr").forEach((row) => {
    const open = () => openRecord(Number(row.dataset.rowid));
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });

  const first = payload.offset + 1;
  const last = Math.min(payload.offset + payload.rows.length, payload.total);
  byId("pageRange").textContent = `${first.toLocaleString()}–${last.toLocaleString()} of ${payload.total.toLocaleString()}`;
  byId("previousPage").disabled = payload.offset === 0;
  byId("nextPage").disabled = payload.offset + payload.rows.length >= payload.total;
}

async function loadRecords() {
  if (!state.activeTable) return;
  byId("recordsLoading").classList.remove("hidden");
  byId("recordsEmpty").classList.add("hidden");
  byId("tableShell").classList.add("hidden");
  byId("pagination").classList.add("hidden");
  const parameters = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.offset),
  });
  if (state.search) parameters.set("search", state.search);
  try {
    state.table = await api(`/internal/admin/database/${encodeURIComponent(state.activeTable)}?${parameters}`);
    renderRecords();
  } catch (error) {
    notify(error.message, true);
    byId("recordsEmpty").classList.remove("hidden");
    byId("recordsEmpty").querySelector("h3").textContent = "Unable to load records";
    byId("recordsEmpty").querySelector("p").textContent = error.message;
  } finally {
    byId("recordsLoading").classList.add("hidden");
  }
}

function formattedRecordValue(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object" && "preview" in value) {
    return `${value.preview}${value.truncated ? `\n\n[Truncated from ${value.characters} characters]` : ""}`;
  }
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    const parsed = JSON.parse(value);
    return JSON.stringify(parsed, null, 2);
  } catch (_) {
    return value;
  }
}

async function openRecord(rowId) {
  byId("recordDrawerTitle").textContent = `${collectionMetadata(state.activeTable)?.label || state.activeTable} · row ${rowId}`;
  byId("recordDrawerBody").innerHTML = `<div class="records-loading"><i></i><span>Loading record…</span></div>`;
  byId("recordScrim").classList.remove("hidden");
  byId("recordDrawer").classList.add("open");
  byId("recordDrawer").setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  try {
    const payload = await api(`/internal/admin/database/${encodeURIComponent(state.activeTable)}/records/${rowId}`);
    byId("recordDrawerBody").innerHTML = Object.entries(payload.record).map(([key, value]) => `
      <section class="record-property"><span>${escapeHTML(key)}</span><pre>${escapeHTML(formattedRecordValue(value))}</pre></section>
    `).join("");
  } catch (error) {
    byId("recordDrawerBody").innerHTML = `<div class="records-empty"><h3>Unable to load record</h3><p>${escapeHTML(error.message)}</p></div>`;
  }
}

function closeRecord() {
  byId("recordDrawer").classList.remove("open");
  byId("recordDrawer").setAttribute("aria-hidden", "true");
  byId("recordScrim").classList.add("hidden");
  document.body.style.overflow = "";
}

function fieldInput(field) {
  const id = `config-${field.key}`;
  const managed = state.config?.runtime_managed;
  const common = `id="${id}" name="${escapeHTML(field.key)}" data-key="${escapeHTML(field.key)}" data-original="${escapeHTML(field.value)}" ${managed ? "disabled" : ""}`;
  if (field.kind === "boolean") {
    return `<select ${common}><option value="true" ${field.value === "true" ? "selected" : ""}>Enabled</option><option value="false" ${field.value === "false" ? "selected" : ""}>Disabled</option></select>`;
  }
  if (field.choices?.length) {
    return `<select ${common}>${field.choices.map((choice) => `<option value="${escapeHTML(choice)}" ${field.value === choice ? "selected" : ""}>${escapeHTML(choice)}</option>`).join("")}</select>`;
  }
  const inputType = field.secret ? "password" : field.kind === "number" ? "number" : field.kind === "email" ? "email" : field.kind === "url" ? "url" : "text";
  const bounds = `${field.minimum !== null ? ` min="${field.minimum}"` : ""}${field.maximum !== null ? ` max="${field.maximum}"` : ""}`;
  const placeholder = field.secret && field.configured ? "Configured — enter to replace" : field.secret ? "Not configured" : "";
  return `<input ${common} type="${inputType}" value="${field.secret ? "" : escapeHTML(field.value)}" placeholder="${placeholder}"${bounds} ${field.secret ? 'autocomplete="new-password"' : ""} />${field.secret ? `<button class="clear-secret" type="button" data-clear-secret="${escapeHTML(field.key)}" ${managed ? "disabled" : ""}>Clear saved</button>` : ""}`;
}

function renderConfiguration() {
  const groups = new Map();
  state.config.fields.forEach((field) => {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  });

  byId("envState").textContent = state.config.runtime_managed
    ? "Provider managed"
    : state.config.env_exists ? ".env connected" : ".env will be created";
  byId("envState").title = state.config.env_file;
  byId("configDescription").textContent = state.config.configuration_note;
  byId("configNavigation").innerHTML = [...groups].map(([group, fields]) => `
    <a href="#config-${escapeHTML(group.toLowerCase().replaceAll(" ", "-"))}">${escapeHTML(group)}<span>${fields.length}</span></a>
  `).join("");
  byId("configGroups").innerHTML = [...groups].map(([group, fields]) => {
    const anchor = `config-${group.toLowerCase().replaceAll(" ", "-")}`;
    return `
      <section id="${escapeHTML(anchor)}" class="config-group">
        <header><h3>${escapeHTML(group)}</h3><span>${fields.length} setting${fields.length === 1 ? "" : "s"}</span></header>
        <div class="field-grid">
          ${fields.map((field) => `
            <div class="config-field">
              <div class="field-head">
                <label for="config-${escapeHTML(field.key)}">${escapeHTML(field.label)}</label>
                <span class="field-status">
                  ${field.secret && field.configured ? `<span class="configured-badge" data-secret-badge="${escapeHTML(field.key)}">Configured</span>` : `<span class="source-badge">${escapeHTML(field.source)}</span>`}
                </span>
              </div>
              <div class="config-control">${fieldInput(field)}</div>
              <small>${escapeHTML(field.help || field.key)}</small>
            </div>
          `).join("")}
        </div>
      </section>
    `;
  }).join("");

  byId("configGroups").querySelectorAll("[data-key]").forEach((input) => {
    input.addEventListener("input", () => updateDirtyField(input));
    input.addEventListener("change", () => updateDirtyField(input));
  });
  byId("configGroups").querySelectorAll("[data-clear-secret]").forEach((button) => {
    button.addEventListener("click", () => toggleClearSecret(button.dataset.clearSecret));
  });
  renderOAuth(state.config.google_oauth);
  renderLinkedInOAuth(state.config.linkedin_oauth);
  byId("oauthFile").disabled = state.config.runtime_managed;
  byId("oauthFileButton").classList.toggle("disabled", state.config.runtime_managed);
  byId("linkedinConnect").disabled = state.config.runtime_managed || byId("linkedinConnect").disabled;
  updateChangeState();
}

function renderOAuth(oauth) {
  const status = byId("oauthStatus");
  status.textContent = oauth.client_uploaded ? "Client uploaded" : "Not uploaded";
  status.className = `status-badge ${oauth.client_uploaded ? "success" : "neutral"}`;
  byId("oauthFilename").textContent = oauth.client_uploaded ? "google-oauth-client.json is ready" : "No client file uploaded";
  byId("oauthPath").textContent = oauth.client_file;
  byId("oauthPath").title = oauth.client_file;
  byId("oauthCommand").textContent = oauth.authorization_command;
  byId("oauthCommandBlock").classList.toggle("hidden", !oauth.client_uploaded);
}

function renderLinkedInOAuth(oauth) {
  const status = byId("linkedinOauthStatus");
  const connected = oauth.authorized && !oauth.expired;
  status.textContent = !oauth.workflow_enabled ? "Paused" : connected ? "Connected" : oauth.expired ? "Expired" : oauth.configured ? "Ready" : "Not configured";
  status.className = `status-badge ${connected ? "success" : oauth.expired ? "warning" : "neutral"}`;
  byId("linkedinOauthSummary").textContent = !oauth.workflow_enabled
    ? "Temporarily disabled — Notion remains the final publishing step"
    : connected
    ? `Authorized profile · ${oauth.person_urn}`
    : oauth.expired
      ? "Authorization expired — reconnect the profile"
      : oauth.configured
        ? "App configured — authorize the publishing profile"
        : "Save the LinkedIn app credentials and restart first";
  byId("linkedinTokenPath").textContent = oauth.token_file;
  byId("linkedinTokenPath").title = oauth.token_file;
  const connect = byId("linkedinConnect");
  connect.disabled = !oauth.workflow_enabled || !oauth.configured;
  connect.textContent = !oauth.workflow_enabled ? "LinkedIn paused" : connected || oauth.expired ? "Reconnect LinkedIn" : "Connect LinkedIn";
  connect.dataset.url = oauth.connect_url;
}

function connectLinkedIn() {
  const button = byId("linkedinConnect");
  if (button.disabled || !button.dataset.url) return;
  button.disabled = true;
  button.textContent = "Opening LinkedIn…";
  window.location.assign(button.dataset.url);
}

async function loadConfiguration() {
  try {
    state.config = await api("/internal/admin/config");
    state.dirty.clear();
    state.clearedSecrets.clear();
    renderConfiguration();
  } catch (error) {
    notify(error.message, true);
    byId("configGroups").innerHTML = `<div class="config-loader"><span>${escapeHTML(error.message)}</span></div>`;
  }
}

function updateDirtyField(input) {
  const key = input.dataset.key;
  if (input.type === "password") {
    if (input.value) state.dirty.add(key);
    else state.dirty.delete(key);
    if (input.value) state.clearedSecrets.delete(key);
  } else if (input.value !== input.dataset.original) {
    state.dirty.add(key);
  } else {
    state.dirty.delete(key);
  }
  input.classList.toggle("dirty", state.dirty.has(key));
  const clearButton = byId("configGroups").querySelector(`[data-clear-secret="${CSS.escape(key)}"]`);
  if (clearButton && input.value) clearButton.classList.remove("active");
  updateSecretBadge(key);
  updateChangeState();
}

function toggleClearSecret(key) {
  const input = byId(`config-${key}`);
  if (state.clearedSecrets.has(key)) {
    state.clearedSecrets.delete(key);
  } else {
    state.clearedSecrets.add(key);
    state.dirty.delete(key);
    input.value = "";
    input.classList.add("dirty");
  }
  const button = byId("configGroups").querySelector(`[data-clear-secret="${CSS.escape(key)}"]`);
  button.classList.toggle("active", state.clearedSecrets.has(key));
  button.textContent = state.clearedSecrets.has(key) ? "Undo clear" : "Clear saved";
  if (!state.clearedSecrets.has(key)) input.classList.remove("dirty");
  updateSecretBadge(key);
  updateChangeState();
}

function updateSecretBadge(key) {
  const badge = byId("configGroups").querySelector(`[data-secret-badge="${CSS.escape(key)}"]`);
  if (!badge) return;
  const clearing = state.clearedSecrets.has(key);
  badge.textContent = clearing ? "Will clear" : state.dirty.has(key) ? "Will replace" : "Configured";
  badge.classList.toggle("clearing", clearing);
}

function updateChangeState() {
  const count = new Set([...state.dirty, ...state.clearedSecrets]).size;
  if (state.config?.runtime_managed) {
    byId("changeCount").textContent = "Managed in the hosting provider";
    byId("saveConfiguration").disabled = true;
    return;
  }
  byId("changeCount").textContent = count ? `${count} unsaved change${count === 1 ? "" : "s"}` : "No unsaved changes";
  byId("saveConfiguration").disabled = count === 0;
}

async function saveConfiguration(event) {
  event.preventDefault();
  const button = byId("saveConfiguration");
  const values = {};
  state.dirty.forEach((key) => {
    values[key] = byId(`config-${key}`).value;
  });
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const result = await api("/internal/admin/config", {
      method: "PUT",
      body: JSON.stringify({ values, clear_secrets: [...state.clearedSecrets] }),
    });
    if (result.restart_required) {
      state.restartRequired = true;
      byId("restartBanner").classList.remove("hidden");
    }
    notify(`${result.updated.length} configuration value${result.updated.length === 1 ? "" : "s"} saved.`);
    await loadConfiguration();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.textContent = "Save configuration";
    updateChangeState();
  }
}

async function uploadOAuthFile(file) {
  if (!file) return;
  if (file.size > 100000) {
    notify("Google OAuth JSON must be smaller than 100 KB.", true);
    return;
  }
  const button = byId("oauthFileButton");
  button.textContent = "Uploading…";
  try {
    const document = JSON.parse(await file.text());
    const result = await api("/internal/admin/google-oauth-client", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, document }),
    });
    renderOAuth({
      ...state.config.google_oauth,
      client_uploaded: true,
      client_file: result.client_file,
      authorization_command: result.authorization_command,
    });
    state.config.google_oauth.client_uploaded = true;
    notify("Google OAuth client uploaded securely.");
  } catch (error) {
    notify(error instanceof SyntaxError ? "That file is not valid JSON." : error.message, true);
  } finally {
    button.innerHTML = "<span>⇧</span> Upload Google JSON";
    byId("oauthFile").value = "";
  }
}

async function copyOAuthCommand() {
  try {
    await navigator.clipboard.writeText(byId("oauthCommand").textContent);
    notify("Authorization command copied.");
  } catch (_) {
    notify("Copy failed. Select the command manually.", true);
  }
}

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
});
byId("refreshDatabase").addEventListener("click", () => loadDatabase());
byId("databaseSearch").addEventListener("submit", (event) => {
  event.preventDefault();
  state.search = byId("databaseSearchInput").value.trim();
  state.offset = 0;
  loadRecords();
});
byId("databaseSearchInput").addEventListener("input", (event) => {
  byId("clearDatabaseSearch").classList.toggle("hidden", !event.target.value);
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.search = event.target.value.trim();
    state.offset = 0;
    loadRecords();
  }, 350);
});
byId("clearDatabaseSearch").addEventListener("click", () => {
  byId("databaseSearchInput").value = "";
  byId("clearDatabaseSearch").classList.add("hidden");
  state.search = "";
  state.offset = 0;
  loadRecords();
});
byId("previousPage").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadRecords();
});
byId("nextPage").addEventListener("click", () => {
  state.offset += state.limit;
  loadRecords();
});
byId("closeRecordDrawer").addEventListener("click", closeRecord);
byId("recordScrim").addEventListener("click", closeRecord);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeRecord();
});
byId("configForm").addEventListener("submit", saveConfiguration);
byId("oauthFile").addEventListener("change", (event) => uploadOAuthFile(event.target.files[0]));
byId("copyOauthCommand").addEventListener("click", copyOAuthCommand);
byId("linkedinConnect").addEventListener("click", connectLinkedIn);

selectTab(window.location.hash === "#configuration" ? "configuration" : "database", false);
if (new URLSearchParams(window.location.search).get("linkedin") === "connected") {
  notify("LinkedIn profile connected successfully.");
  window.history.replaceState(null, "", "/admin/#configuration");
}
