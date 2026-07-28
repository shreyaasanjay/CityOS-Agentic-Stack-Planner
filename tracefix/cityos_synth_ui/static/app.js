const state = {
  workspaces: [],
  result: null,
  webRun: null,
  webRunning: false,
  logs: [],
};

const els = {
  form: document.querySelector("#synthForm"),
  workspaceSelect: document.querySelector("#workspaceSelect"),
  workspacePath: document.querySelector("#workspacePath"),
  cityosRoot: document.querySelector("#cityosRoot"),
  appsDir: document.querySelector("#appsDir"),
  packageName: document.querySelector("#packageName"),
  overwrite: document.querySelector("#overwrite"),
  webDataUrl: document.querySelector("#webDataUrl"),
  rawDataJson: document.querySelector("#rawDataJson"),
  rawDataFile: document.querySelector("#rawDataFile"),
  rawDataStatus: document.querySelector("#rawDataStatus"),
  clearRawData: document.querySelector("#clearRawData"),
  sourceMode: document.querySelector("#sourceMode"),
  webTimeout: document.querySelector("#webTimeout"),
  autoRunWebData: document.querySelector("#autoRunWebData"),
  runWebData: document.querySelector("#runWebData"),
  refresh: document.querySelector("#refresh"),
  synthesize: document.querySelector("#synthesize"),
  statusText: document.querySelector("#statusText"),
  statusBadge: document.querySelector("#statusBadge"),
  appCount: document.querySelector("#appCount"),
  agentCount: document.querySelector("#agentCount"),
  manifestPath: document.querySelector("#manifestPath"),
  appsDirLabel: document.querySelector("#appsDirLabel"),
  appsList: document.querySelector("#appsList"),
  answerSummary: document.querySelector("#answerSummary"),
  webRunLabel: document.querySelector("#webRunLabel"),
  log: document.querySelector("#log"),
  toast: document.querySelector("#toast"),
};

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function showToast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 1900);
}

function formatRawDataSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

function rawDataStatusText(fileNames, fileSize, hasData) {
  if (!hasData) return "No JSON files selected";
  const names = Array.isArray(fileNames) ? fileNames.filter(Boolean) : [fileNames].filter(Boolean);
  const sizeText = formatRawDataSize(fileSize || 0);
  if (names.length <= 1) return `Loaded ${names[0] || "JSON file"} (${sizeText})`;
  const preview = names.slice(0, 3).join(", ");
  const suffix = names.length > 3 ? `, +${names.length - 3} more` : "";
  return `Loaded ${names.length} JSON files (${sizeText}): ${preview}${suffix}`;
}

function setRawDataPayload(raw, fileNames = [], fileSize = 0) {
  const value = String(raw || "");
  const hasData = value.trim().length > 0;
  if (els.rawDataJson) els.rawDataJson.value = value;
  if (els.rawDataStatus) {
    els.rawDataStatus.textContent = rawDataStatusText(fileNames, fileSize || value.length, hasData);
  }
  if (els.clearRawData) els.clearRawData.disabled = !hasData;
}

function clearRawDataUpload() {
  if (els.rawDataFile) els.rawDataFile.value = "";
  setRawDataPayload("");
}

async function loadRawDataFile() {
  const files = Array.from(els.rawDataFile?.files || []);
  if (!files.length) return;
  try {
    const loaded = [];
    for (const file of files) {
      const raw = await file.text();
      loaded.push({ name: file.name, size: file.size, raw, data: JSON.parse(raw) });
    }
    const rawPayload = loaded.length === 1
      ? loaded[0].raw
      : JSON.stringify({
          kind: "tracefix.raw-json-bundle.v1",
          fileCount: loaded.length,
          files: loaded.map(({ name, size, data }) => ({ name, size, data })),
        }, null, 2);
    const names = loaded.map((item) => item.name);
    const totalSize = loaded.reduce((sum, item) => sum + item.size, 0);
    setRawDataPayload(rawPayload, names, totalSize);
    showToast(loaded.length === 1 ? `Loaded ${loaded[0].name}` : `Loaded ${loaded.length} JSON files`);
  } catch (error) {
    if (els.rawDataFile) els.rawDataFile.value = "";
    showToast(`JSON files could not be loaded: ${error.message}`);
  }
}
function setStatus(status, label = status) {
  els.statusText.textContent = label;
  els.statusBadge.textContent = status;
  els.statusBadge.className = `badge ${status.toLowerCase()}`;
}

function log(line) {
  state.logs.push(line);
  els.log.textContent = state.logs.join("\n");
  els.log.scrollTop = els.log.scrollHeight;
}

async function loadConfig() {
  const config = await getJson("/api/config");
  els.cityosRoot.value = config.cityosRoot || "";
  els.appsDir.value = config.appsDir || "";
  els.webDataUrl.value = config.webDataUrl || "https://smartroom-mirror.vercel.app/api/v1";
  state.workspaces = config.workspaces || [];
  renderWorkspaces();
}

async function refreshWorkspaces() {
  const data = await getJson("/api/workspaces");
  state.workspaces = data.workspaces || [];
  renderWorkspaces();
  showToast("Workspaces refreshed");
}

function renderWorkspaces() {
  els.workspaceSelect.innerHTML = state.workspaces.length
    ? state.workspaces.map((workspace) => {
        const ready = workspace.productionReady ? "verified" : workspace.verificationStatus;
        const label = `${workspace.name} (${ready})`;
        return `<option value="${escapeHtml(workspace.path)}">${escapeHtml(label)}</option>`;
      }).join("")
    : `<option value="">No workspaces found</option>`;

  if (state.workspaces[0]) {
    els.workspaceSelect.value = state.workspaces[0].path;
    els.workspacePath.value = state.workspaces[0].path;
    updateWorkspaceMetrics(state.workspaces[0]);
  }
}

function updateWorkspaceMetrics(workspace) {
  els.agentCount.textContent = String((workspace.agents || []).filter(Boolean).length);
  els.statusText.textContent = workspace.productionReady
    ? "Verified workspace selected"
    : `Selected workspace: ${workspace.verificationStatus || "unknown"}`;
}

function bindEvents() {
  els.workspaceSelect.addEventListener("change", () => {
    els.workspacePath.value = els.workspaceSelect.value;
    const selected = state.workspaces.find((workspace) => workspace.path === els.workspaceSelect.value);
    if (selected) updateWorkspaceMetrics(selected);
  });

  els.cityosRoot.addEventListener("input", () => {
    if (!els.appsDir.value || els.appsDir.value.endsWith("\\apps") || els.appsDir.value.endsWith("/apps")) {
      const root = els.cityosRoot.value.trim();
      els.appsDir.value = root ? `${root.replace(/[\\/]$/, "")}\\apps` : "";
    }
  });

  els.refresh.addEventListener("click", async () => {
    try {
      await refreshWorkspaces();
    } catch (error) {
      showToast(error.message);
    }
  });

  els.rawDataFile?.addEventListener("change", loadRawDataFile);
  els.clearRawData?.addEventListener("click", clearRawDataUpload);

  els.runWebData.addEventListener("click", async () => {
    await runWebData();
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await synthesize();
  });
}

async function synthesize() {
  state.logs = [];
  state.result = null;
  state.webRun = null;
  renderResult();
  renderWebRun();
  setStatus("Running", "Synthesis running");
  els.synthesize.disabled = true;
  log(`Workspace: ${els.workspacePath.value}`);
  log(`CityOS apps: ${els.appsDir.value}`);
  try {
    const result = await postJson("/api/synthesize", {
      workspacePath: els.workspacePath.value,
      cityosRoot: els.cityosRoot.value,
      appsDir: els.appsDir.value,
      packageName: els.packageName.value,
      overwrite: els.overwrite.checked,
    });
    state.result = result;
    log(`Plan: ${result.planPath}`);
    log(`Manifest: ${result.manifestPath}`);
    result.apps.forEach((app) => log(`App: ${app.name} -> ${app.path}`));
    setStatus("Done", "Synthesis completed");
    renderResult();
    if (els.autoRunWebData.checked) {
      await runWebData();
    }
  } catch (error) {
    log(`Error: ${error.message}`);
    setStatus("Failed", "Synthesis failed");
    showToast(error.message);
  } finally {
    els.synthesize.disabled = false;
    renderResult();
  }
}

async function runWebData() {
  const manifestPath = state.result?.manifestPath;
  if (!manifestPath) {
    showToast("Synthesize CityOS apps first");
    return;
  }
  state.webRunning = true;
  state.webRun = null;
  renderResult();
  renderWebRun();
  setStatus("Running", "Smartroom agents running");
  const rawDataJson = (els.rawDataJson?.value || "").trim();
  log(rawDataJson ? "Uploaded JSON data" : `Web data source: ${els.webDataUrl.value}`);
  log(`Manifest for data run: ${manifestPath}`);
  try {
    const result = await postJson("/api/run-web-data", {
      manifestPath,
      sourceUrl: els.webDataUrl.value,
      sourceMode: rawDataJson ? "raw-json" : els.sourceMode.value,
      rawDataJson,
      timeoutSeconds: Number(els.webTimeout.value || 30),
    });
    state.webRun = result;
    log(`Web data result: ${result.resultPath}`);
    log(`Payload: ${result.payload?.payloadPath || "not written"}`);
    if (result.answerPath) log(`Answer: ${result.answerPath}`);
    (result.runs || []).forEach((run) => {
      const answerSuffix = run.answerPath ? ` answer=${run.answerPath}` : "";
      log(`Agent run: ${run.app} ${run.status}${answerSuffix}`);
    });
    setStatus(result.ok ? "Done" : "Failed", result.ok ? "Smartroom answer ready" : "Smartroom run had errors");
    renderWebRun();
    showToast(result.ok ? "Smartroom answer ready" : "Smartroom run finished with errors");
  } catch (error) {
    log(`Smartroom error: ${error.message}`);
    setStatus("Failed", "Smartroom run failed");
    showToast(error.message);
  } finally {
    state.webRunning = false;
    renderResult();
    renderWebRun();
  }
}

function renderResult() {
  const result = state.result;
  const apps = result?.apps || [];
  els.appCount.textContent = String(apps.length);
  els.manifestPath.textContent = result?.manifestPath || "No synthesis yet";
  els.appsDirLabel.textContent = result?.appsDir || "";
  els.runWebData.disabled = !result?.manifestPath || state.webRunning;
  els.runWebData.textContent = state.webRunning ? "Running Smartroom Agents" : "Run Smartroom Agents";
  if (!apps.length) {
    els.appsList.innerHTML = `<div class="empty">No apps generated yet.</div>`;
    return;
  }
  els.appsList.innerHTML = apps.map((app) => `
    <article class="app-card">
      <div>
        <span>${escapeHtml(app.kind)}</span>
        <h4>${escapeHtml(app.name)}</h4>
        <p>${escapeHtml(app.path)}</p>
      </div>
      <code>${escapeHtml(app.buildCommand)}</code>
    </article>
  `).join("");
}

function renderWebRun() {
  const result = state.webRun;
  if (state.webRunning) {
    els.webRunLabel.textContent = els.webDataUrl.value;
    els.answerSummary.className = "answer-summary pending";
    els.answerSummary.innerHTML = `<div class="empty">Fetching smartroom data and feeding it to the generated apps...</div>`;
    return;
  }
  if (!result) {
    els.webRunLabel.textContent = "";
    els.answerSummary.className = "answer-summary empty";
    els.answerSummary.textContent = "Synthesize apps, then run smartroom agents to generate an answer summary.";
    return;
  }

  const answer = result.answer || result.payload?.answer || null;
  const snapshot = result.payload?.snapshotSummary || {};
  const cameras = Array.isArray(answer?.cameras) ? answer.cameras : [];
  els.webRunLabel.textContent = result.sourceKind || "web data";
  els.answerSummary.className = `answer-summary ${result.ok ? "ok" : "failed"}`;
  const summaryText = answer?.text || "No answer text was returned. Check the payload and run logs.";
  const cameraHtml = cameras.length
    ? `<div class="camera-list">${cameras.map(renderCamera).join("")}</div>`
    : "";
  const runHtml = Array.isArray(result.runs) && result.runs.length
    ? `<div class="run-list">${result.runs.map(renderRun).join("")}</div>`
    : "";
  els.answerSummary.innerHTML = `
    <div class="answer-status ${result.ok ? "ok" : "failed"}">${escapeHtml(result.ok ? "Completed" : "Needs attention")}</div>
    <p class="answer-text">${escapeHtml(summaryText)}</p>
    ${cameraHtml}
    <div class="answer-details">
      ${detailRow("Recording", [snapshot.selectedDay, snapshot.selectedRecording].filter(Boolean).join(" / "))}
      ${detailRow("Cameras", Array.isArray(snapshot.cameras) ? snapshot.cameras.join(", ") : "")}
      ${detailRow("Source", result.sourceUrl)}
      ${detailRow("Payload", result.payload?.payloadPath)}
      ${detailRow("Answer file", result.answerPath)}
      ${detailRow("Run file", result.resultPath)}
      ${detailRow("Output root", result.outputRoot)}
    </div>
    ${runHtml}
  `;
}

function renderCamera(camera) {
  const facts = [];
  if (camera.peakPeople !== undefined && camera.peakPeople !== null) facts.push(`peak ${camera.peakPeople}`);
  if (camera.lastPeople !== undefined && camera.lastPeople !== null) facts.push(`last ${camera.lastPeople}`);
  if (camera.trackCount !== undefined && camera.trackCount !== null) facts.push(`tracks ${camera.trackCount}`);
  if (Array.isArray(camera.actions) && camera.actions.length) facts.push(`actions ${camera.actions.join(", ")}`);
  return `
    <article class="camera-card">
      <strong>${escapeHtml(camera.camera || "camera")}</strong>
      <span>${escapeHtml(facts.join("; ") || "No camera facts returned")}</span>
    </article>
  `;
}

function renderRun(run) {
  return `
    <article class="run-card ${escapeHtml(run.status || "unknown")}">
      <strong>${escapeHtml(run.app || "app")}</strong>
      <span>${escapeHtml(run.status || "unknown")}</span>
      ${run.answerPath ? `<code>${escapeHtml(run.answerPath)}</code>` : ""}
    </article>
  `;
}

function detailRow(label, value) {
  if (value === undefined || value === null || value === "") return "";
  return `
    <div class="answer-detail">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(value)}</code>
    </div>
  `;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function init() {
  bindEvents();
  renderResult();
  renderWebRun();
  await loadConfig();
}

init().catch((error) => {
  setStatus("Failed", "Startup failed");
  log(error.message);
});
