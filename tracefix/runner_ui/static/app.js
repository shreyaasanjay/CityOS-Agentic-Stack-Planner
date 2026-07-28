const TELLME_LLM_TIMEOUT_SECONDS = 120;

const state = {
  workflow: "tellme",
  tasks: [],
  runMode: "design",
  taskMode: "benchmark",
  runId: null,
  eventSource: null,
  logs: [],
  turns: 0,
  tools: 0,
  artifacts: {},
  activeView: "log",
  usage: null,
  usageDetailsOpen: false,
  workflowSeen: { tellme: true, planner: false, synth: false },
  workflowReady: { tellme: false, planner: false, synth: false },
  tellme: {
    current: null,
    runId: null,
    apiKeyDetected: false,
    envKeyDetected: false,
    model: "gpt-4.1-mini",
    context: null,
    taskText: "",  // actual TeLLMe spec text — set on handoff, used by startRun()
  },
  synth: {
    workspaces: [],
    selected: null,
    result: null,
    workspaceType: "custom",
    cityosRoot: "",
    appsDir: "",
    webDataUrl: "https://smartroom-mirror.vercel.app/api/v1",
  },
};

const THEME_STORAGE_KEY = "tracefix-runner-theme";
const PROCEDURE_LABELS = {
  single_agent_generation: "Single Agent Generation",
  exact_reuse: "Exact Reuse",
  parameterized_reuse: "Parameterized Reuse",
  partial_recomposition: "Partial Recomposition",
  full_generation: "Full Generation",
};

const els = {
  themeToggle: document.querySelector("#themeToggle"),
  themeToggleText: document.querySelector("#themeToggleText"),
  tellmeForm: document.querySelector("#tellmeForm"),
  tellmeMode: document.querySelector("#tellmeMode"),
  tellmeModel: document.querySelector("#tellmeModel"),
  tellmeApiKey: document.querySelector("#tellmeApiKey"),
  tellmeKeyRow: document.querySelector("#tellmeKeyRow"),
  tellmeKeyStatus: document.querySelector("#tellmeKeyStatus"),
  uploadJsonButton: document.querySelector("#uploadJsonButton"),
  jsonContextFile: document.querySelector("#jsonContextFile"),
  jsonContextStatus: document.querySelector("#jsonContextStatus"),
  tellmeQuery: document.querySelector("#tellmeQuery"),
  tellmeSpaceId: document.querySelector("#tellmeSpaceId"),
  tellmeWebDataUrl: document.querySelector("#tellmeWebDataUrl"),
  tellmeRawDataJson: document.querySelector("#tellmeRawDataJson"),
  tellmeRawDataFile: document.querySelector("#tellmeRawDataFile"),
  tellmeRawDataStatus: document.querySelector("#tellmeRawDataStatus"),
  tellmeClearRawData: document.querySelector("#tellmeClearRawData"),
  tellmeTimestamp: document.querySelector("#tellmeTimestamp"),
  tellmeProcess: document.querySelector("#tellmeProcess"),
  tellmeRunAll: document.querySelector("#tellmeRunAll"),
  tellmeTracefixProvider: document.querySelector("#tellmeTracefixProvider"),
  tellmeTracefixModel: document.querySelector("#tellmeTracefixModel"),
  tellmeTracefixApiKey: document.querySelector("#tellmeTracefixApiKey"),
  tellmeTracefixKeyLabel: document.querySelector("#tellmeTracefixKeyLabel"),
  tellmeTracefixOllamaLabel: document.querySelector("#tellmeTracefixOllamaLabel"),
  tellmeTracefixOllamaUrl: document.querySelector("#tellmeTracefixOllamaUrl"),
  tellmeTracefixKeyStatus: document.querySelector("#tellmeTracefixKeyStatus"),
  tellmeToTracefix: document.querySelector("#tellmeToTracefix"),
  tellmePanel: document.querySelector("#tellmePanel"),
  tellmeRoute: document.querySelector("#tellmeRoute"),
  tellmeRationale: document.querySelector("#tellmeRationale"),
  tellmePrivacy: document.querySelector("#tellmePrivacy"),
  tellmePrivacyScope: document.querySelector("#tellmePrivacyScope"),
  tellmeTaskCount: document.querySelector("#tellmeTaskCount"),
  tellmeHarnessCount: document.querySelector("#tellmeHarnessCount"),
  tellmeRunStatus: document.querySelector("#tellmeRunStatus"),
  tellmeRunId: document.querySelector("#tellmeRunId"),
  tellmeMessages: document.querySelector("#tellmeMessages"),
  tellmeIntent: document.querySelector("#tellmeIntent"),
  tellmeTaskSpec: document.querySelector("#tellmeTaskSpec"),
  tellmeHandoffMeta: document.querySelector("#tellmeHandoffMeta"),
  tellmeAnswer: document.querySelector("#tellmeAnswer"),
  tellmeChatAnswer: document.querySelector("#tellmeChatAnswer"),
  form: document.querySelector("#runForm"),
  provider: document.querySelector("#provider"),
  providerFields: document.querySelector("#providerFields"),
  keyFields: document.querySelector("#keyFields"),
  model: document.querySelector("#model"),
  modelSuggestions: document.querySelector("#modelSuggestions"),
  openaiKey: document.querySelector("#openaiKey"),
  anthropicKey: document.querySelector("#anthropicKey"),
  openrouterKey: document.querySelector("#openrouterKey"),
  ollamaUrl: document.querySelector("#ollamaUrl"),
  taskId: document.querySelector("#taskId"),
  taskSourceFields: document.querySelector("#taskSourceFields"),
  benchmarkField: document.querySelector("#benchmarkField"),
  customTaskField: document.querySelector("#customTaskField"),
  customTask: document.querySelector("#customTask"),
  runtimeFields: document.querySelector("#runtimeFields"),
  runtimeWorkspaceField: document.querySelector("#runtimeWorkspaceField"),
  workspacePathInput: document.querySelector("#workspacePathInput"),
  legacyRuntimeOptions: document.querySelector("#legacyRuntimeOptions"),
  harness: document.querySelector("#harness"),
  runtimeTask: document.querySelector("#runtimeTask"),
  maxTurns: document.querySelector("#maxTurns"),
  maxTokens: document.querySelector("#maxTokens"),
  temperature: document.querySelector("#temperature"),
  tracefixRunFields: document.querySelector("#tracefixRunFields"),
  opencodeBin: document.querySelector("#opencodeBin"),
  timeout: document.querySelector("#timeout"),
  live: document.querySelector("#live"),
  noSummarize: document.querySelector("#noSummarize"),
  batchLint: document.querySelector("#batchLint"),
  startRun: document.querySelector("#startRun"),
  stopRun: document.querySelector("#stopRun"),
  runMeta: document.querySelector("#runMeta"),
  runTitle: document.querySelector("#runTitle"),
  uiBuildStamp: document.querySelector("#uiBuildStamp"),
  statusBadge: document.querySelector("#statusBadge"),
  turnCount: document.querySelector("#turnCount"),
  toolCount: document.querySelector("#toolCount"),
  artifactCount: document.querySelector("#artifactCount"),
  workspacePath: document.querySelector("#workspacePathDisplay"),
  llmUsageCard: document.querySelector("#llmUsageCard"),
  llmUsageDetails: document.querySelector("#llmUsageDetails"),
  llmCost: document.querySelector("#llmCost"),
  llmTokenSummary: document.querySelector("#llmTokenSummary"),
  llmModelName: document.querySelector("#llmModelName"),
  llmUsageSource: document.querySelector("#llmUsageSource"),
  usageInputTokens: document.querySelector("#usageInputTokens"),
  usageOutputTokens: document.querySelector("#usageOutputTokens"),
  usageTotalTokens: document.querySelector("#usageTotalTokens"),
  usageCost: document.querySelector("#usageCost"),
  usageModelBreakdown: document.querySelector("#usageModelBreakdown"),
  usageDesignTokens: document.querySelector("#usageDesignTokens"),
  usageDesignCost: document.querySelector("#usageDesignCost"),
  usageRepairTokens: document.querySelector("#usageRepairTokens"),
  usageRepairCost: document.querySelector("#usageRepairCost"),
  usageVerificationTokens: document.querySelector("#usageVerificationTokens"),
  usageVerificationCost: document.querySelector("#usageVerificationCost"),
  usageTotalRuns: document.querySelector("#usageTotalRuns"),
  usageSessionTokens: document.querySelector("#usageSessionTokens"),
  usageSessionCost: document.querySelector("#usageSessionCost"),
  timeline: document.querySelector("#timeline"),
  graph: document.querySelector("#topologyGraph"),
  graphStatus: document.querySelector("#graphStatus"),
  refreshArtifacts: document.querySelector("#refreshArtifacts"),
  openWorkspace: document.querySelector("#openWorkspace"),
  openTlcError: document.querySelector("#openTlcError"),
  openSpecFolder: document.querySelector("#openSpecFolder"),
  outputView: document.querySelector("#outputView"),
  toast: document.querySelector("#toast"),
  runForm: document.querySelector("#runForm"),
  synthControls: document.querySelector("#synthControls"),
  synthPanel: document.querySelector("#synthPanel"),
  synthWorkspaceTypeGroup: document.querySelector("#synthWorkspaceTypeGroup"),
  synthBenchmarkField: document.querySelector("#synthBenchmarkField"),
  synthBenchmarkSelect: document.querySelector("#synthBenchmarkSelect"),
  synthWorkspaceSelect: document.querySelector("#synthWorkspaceSelect"),
  synthWorkspacePath: document.querySelector("#synthWorkspacePath"),
  synthCityOSRoot: document.querySelector("#synthCityOSRoot"),
  synthWebDataUrl: document.querySelector("#synthWebDataUrl"),
  synthRawDataJson: document.querySelector("#synthRawDataJson"),
  synthRawDataFile: document.querySelector("#synthRawDataFile"),
  synthRawDataStatus: document.querySelector("#synthRawDataStatus"),
  synthClearRawData: document.querySelector("#synthClearRawData"),
  synthOutputDir: document.querySelector("#synthOutputDir"),
  synthPackageName: document.querySelector("#synthPackageName"),
  synthOverwrite: document.querySelector("#synthOverwrite"),
  synthRefresh: document.querySelector("#synthRefresh"),
  synthGenerate: document.querySelector("#synthGenerate"),
  synthBuildCityOS: document.querySelector("#synthBuildCityOS"),
  synthRunWebData: document.querySelector("#synthRunWebData"),
  synthViewTellMeAnswer: document.querySelector("#synthViewTellMeAnswer"),
  synthStatus: document.querySelector("#synthStatus"),
  synthChecklist: document.querySelector("#synthChecklist"),
  synthWorkspaceTypeDisplay: document.querySelector("#synthWorkspaceTypeDisplay"),
  synthTaskTextDisplay: document.querySelector("#synthTaskTextDisplay"),
  synthWorkspaceDisplay: document.querySelector("#synthWorkspaceDisplay"),
  synthPlanDisplay: document.querySelector("#synthPlanDisplay"),
  synthAgentsDisplay: document.querySelector("#synthAgentsDisplay"),
  synthChannelsDisplay: document.querySelector("#synthChannelsDisplay"),
  synthResourcesDisplay: document.querySelector("#synthResourcesDisplay"),
  synthOutputStatus: document.querySelector("#synthOutputStatus"),
  synthAppCount: document.querySelector("#synthAppCount"),
  synthManifestPath: document.querySelector("#synthManifestPath"),
  synthAppsDirLabel: document.querySelector("#synthAppsDirLabel"),
  synthAppsStatus: document.querySelector("#synthAppsStatus"),
  synthAppsList: document.querySelector("#synthAppsList"),
  synthBuildCommands: document.querySelector("#synthBuildCommands"),
  synthOutput: document.querySelector("#synthOutput"),
};

const modelDefaults = {
  openai: "gpt-5-mini",
  anthropic: "claude-sonnet-4-5-20250929",
  openrouter: "z-ai/glm-5.2",
  ollama: "llama3.2:3b",
};

let modelOptions = {
  openai: [
    "gpt-5-mini",
    "gpt-5",
    "gpt-4.1-mini",
    "gpt-4.1",
  ],
  anthropic: [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-1-20250805",
    "claude-3-5-haiku-20241022",
  ],
  openrouter: [
    "z-ai/glm-5.2",
    "openai/gpt-4.1-mini",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-r1",
  ],
  ollama: [
    "llama3.2:3b",
    "llama3.1:8b",
    "qwen2.5-coder:7b",
    "mistral:7b",
  ],
};

function showToast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 1800);
}

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.errors?.join("; ") || `Request failed: ${response.status}`);
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.errors?.join("; ") || `Request failed: ${response.status}`);
  return data;
}

function readStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

function getPreferredTheme() {
  return readStoredTheme() || (window.matchMedia?.("(prefers-color-scheme: light)")?.matches ? "light" : "dark");
}

function writeStoredTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Theme preference is non-critical; blocked storage should not affect the runner.
  }
}

function applyTheme(theme) {
  const normalized = theme === "light" ? "light" : "dark";
  const nextMode = normalized === "light" ? "dark" : "light";
  const label = `Switch to ${nextMode} mode`;

  document.documentElement.dataset.theme = normalized;
  if (els.themeToggle) {
    els.themeToggle.setAttribute("aria-label", label);
    els.themeToggle.title = label;
  }
  if (els.themeToggleText) {
    els.themeToggleText.textContent = nextMode === "light" ? "Light" : "Dark";
  }
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const next = current === "light" ? "dark" : "light";
  writeStoredTheme(next);
  applyTheme(next);
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
  [els.tellmeRawDataJson, els.synthRawDataJson].forEach((field) => {
    if (field) field.value = value;
  });
  const statusText = rawDataStatusText(fileNames, fileSize || value.length, hasData);
  [els.tellmeRawDataStatus, els.synthRawDataStatus].forEach((status) => {
    if (status) status.textContent = statusText;
  });
  [els.tellmeClearRawData, els.synthClearRawData].forEach((button) => {
    if (button) button.disabled = !hasData;
  });
}

function clearRawDataUpload() {
  [els.tellmeRawDataFile, els.synthRawDataFile].forEach((input) => {
    if (input) input.value = "";
  });
  setRawDataPayload("");
}

async function loadRawDataFile(input) {
  const files = Array.from(input?.files || []);
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
    input.value = "";
    showToast(`JSON files could not be loaded: ${error.message}`);
  }
}
function bindEvents() {
  els.themeToggle?.addEventListener("click", toggleTheme);
  document.querySelectorAll("[data-workflow]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.workflow = button.dataset.workflow;
      markWorkflowSeen(state.workflow);
      document.querySelectorAll("[data-workflow]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      updateWorkflow();
      if (state.workflow === "tellme") await loadTellMeCurrent();
      if (state.workflow === "synth") await loadSynthConfig();
    });
  });

  els.tellmeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await processTellMeQuery();
  });

  els.tellmeMode.addEventListener("change", () => {
    updateTellMeModeFields();
  });

  els.uploadJsonButton?.addEventListener("click", () => {
    els.jsonContextFile?.click();
  });

  els.jsonContextFile?.addEventListener("change", async () => {
    await uploadJsonContext();
  });

  els.tellmeApiKey.addEventListener("input", () => {
    updateTellMeModeFields();
    updateTellMeTracefixFields();
  });

  els.tellmeWebDataUrl?.addEventListener("input", () => {
    if (els.synthWebDataUrl) els.synthWebDataUrl.value = els.tellmeWebDataUrl.value;
    state.synth.webDataUrl = els.tellmeWebDataUrl.value.trim() || state.synth.webDataUrl;
  });

  els.tellmeRawDataFile?.addEventListener("change", async () => {
    await loadRawDataFile(els.tellmeRawDataFile);
  });

  els.synthRawDataFile?.addEventListener("change", async () => {
    await loadRawDataFile(els.synthRawDataFile);
  });

  els.tellmeClearRawData?.addEventListener("click", clearRawDataUpload);
  els.synthClearRawData?.addEventListener("click", clearRawDataUpload);
  els.tellmeRunAll.addEventListener("click", async () => {
    await processTellMeFullPipeline();
  });

  els.tellmeToTracefix.addEventListener("click", async () => {
    await startTraceFixFromTellMe();
  });

  els.tellmeTracefixProvider.addEventListener("change", () => {
    selectTellMeTracefixProvider(els.tellmeTracefixProvider.value);
  });

  els.tellmeTracefixModel.addEventListener("input", () => {
    els.model.value = els.tellmeTracefixModel.value;
  });

  els.tellmeTracefixApiKey.addEventListener("input", () => {
    writeProviderKey(els.tellmeTracefixProvider.value, els.tellmeTracefixApiKey.value);
    updateTellMeTracefixFields();
  });

  els.tellmeTracefixOllamaUrl.addEventListener("input", () => {
    els.ollamaUrl.value = els.tellmeTracefixOllamaUrl.value;
    updateTellMeTracefixFields();
  });

  els.provider.addEventListener("change", () => {
    const provider = els.provider.value;
    els.model.value = modelDefaults[provider] || (modelOptions[provider] || [])[0] || els.model.value;
    updateModelSuggestions();
    updateKeyFields();
    syncTellMeTracefixFromPlanner();
  });

  [els.model, els.openaiKey, els.anthropicKey, els.openrouterKey, els.ollamaUrl].forEach((field) => {
    field.addEventListener("input", () => syncTellMeTracefixFromPlanner());
  });
  document.querySelectorAll("[data-task-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.taskMode = button.dataset.taskMode;
      document.querySelectorAll("[data-task-mode]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      els.benchmarkField.classList.toggle("hidden", state.taskMode !== "benchmark");
      els.customTaskField.classList.toggle("hidden", state.taskMode !== "custom");
    });
  });

  document.querySelectorAll("[data-run-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.runMode = button.dataset.runMode;
      document.querySelectorAll("[data-run-mode]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      updateModeFields();
    });
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await startRun();
  });

  els.stopRun.addEventListener("click", async () => {
    if (!state.runId) return;
    await postJson(`/api/runs/${state.runId}/stop`, {});
    showToast("Stop requested");
  });

  els.refreshArtifacts.addEventListener("click", async () => {
    await refreshRun();
    showToast("Artifacts refreshed");
  });

  els.openWorkspace.addEventListener("click", async () => {
    const workspacePath = currentWorkspacePath();
    if (!workspacePath) {
      showToast("No workspace selected");
      return;
    }
    try {
      await postJson("/api/open-workspace", { workspacePath });
      showToast("Workspace opened");
    } catch (error) {
      showToast(error.message);
    }
  });

  els.openTlcError.addEventListener("click", async () => {
    await openArtifact("tlc_error", "TLC error opened");
  });

  els.openSpecFolder.addEventListener("click", async () => {
    await openArtifact("spec_dir", "Spec folder opened");
  });

  els.llmUsageCard.addEventListener("click", () => {
    state.usageDetailsOpen = !state.usageDetailsOpen;
    els.llmUsageCard.setAttribute("aria-expanded", String(state.usageDetailsOpen));
    els.llmUsageDetails.classList.toggle("hidden", !state.usageDetailsOpen);
  });

  els.synthWorkspaceTypeGroup.querySelectorAll("[data-workspace-type]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.synth.workspaceType = button.dataset.workspaceType;
      els.synthWorkspaceTypeGroup.querySelectorAll("[data-workspace-type]").forEach((b) => {
        b.classList.toggle("active", b === button);
      });
      const isBenchmark = state.synth.workspaceType === "benchmark";
      els.synthBenchmarkField.classList.toggle("hidden", !isBenchmark);
      await loadSynthConfig({ preferCurrent: false });
    });
  });

  els.synthWorkspaceSelect.addEventListener("change", async () => {
    els.synthWorkspacePath.value = els.synthWorkspaceSelect.value;
    await loadSynthWorkspace(els.synthWorkspacePath.value);
  });

  els.synthBenchmarkSelect.addEventListener("change", async () => {
    if (els.synthBenchmarkSelect.value) {
      els.taskId.value = els.synthBenchmarkSelect.value;
    }
    await loadSynthConfig({ preferCurrent: false });
  });

  els.taskId.addEventListener("change", async () => {
    if (els.synthBenchmarkSelect.value !== els.taskId.value) {
      els.synthBenchmarkSelect.value = els.taskId.value;
    }
    if (state.workflow === "synth") {
      await loadSynthConfig({ preferCurrent: false });
    }
  });

  els.synthRefresh.addEventListener("click", async () => {
    await loadSynthConfig();
    if (els.synthWorkspacePath.value) await loadSynthWorkspace(els.synthWorkspacePath.value);
    showToast("Synth readiness refreshed");
  });

  els.synthGenerate.addEventListener("click", async () => {
    await synthesizeCityOSArtifacts();
  });

  els.synthBuildCityOS.addEventListener("click", async () => {
    await buildCityOSArtifacts();
  });

  els.synthRunWebData?.addEventListener("click", async () => {
    await runWebDataApps();
  });

  els.synthViewTellMeAnswer?.addEventListener("click", async () => {
    await viewTellMeAnswer();
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeView = button.dataset.view;
      document.querySelectorAll("[data-view]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderOutput();
    });
  });
}

function updateWorkflow() {
  const isTellMe = state.workflow === "tellme";
  const isPlanner = state.workflow === "planner";
  const isSynth = state.workflow === "synth";
  els.tellmeForm.classList.toggle("hidden", !isTellMe);
  els.tellmePanel.classList.toggle("hidden", !isTellMe);
  els.runForm.classList.toggle("hidden", !isPlanner);
  els.synthControls.classList.toggle("hidden", !isSynth);
  els.synthPanel.classList.toggle("hidden", !isSynth);
  document.querySelectorAll(".planner-only").forEach((section) => {
    section.classList.toggle("hidden", !isPlanner);
  });
  if (isTellMe) {
    els.runTitle.textContent = "TeLLMe Intent Planner";
    els.runMeta.textContent = "Privacy-bounded natural-language entrypoint";
    els.statusBadge.textContent = state.tellme.current ? "Task ready" : "Idle";
    els.statusBadge.className = `status-badge ${state.tellme.current ? "completed" : "idle"}`;
  } else if (isSynth) {
    els.runTitle.textContent = "CityOS Synthesizer";
    els.runMeta.textContent = "Generate CityOS app artifacts from a verified TraceFix plan";
    els.statusBadge.textContent = "Synthesis";
    els.statusBadge.className = "status-badge idle";
    // Sync workspace type button + benchmark field visibility
    els.synthWorkspaceTypeGroup.querySelectorAll("[data-workspace-type]").forEach((b) => {
      b.classList.toggle("active", b.dataset.workspaceType === state.synth.workspaceType);
    });
    const isBenchmarkMode = state.synth.workspaceType === "benchmark";
    if (els.synthBenchmarkField) els.synthBenchmarkField.classList.toggle("hidden", !isBenchmarkMode);
  } else {
    els.runTitle.textContent = state.runId ? els.runTitle.textContent : "No plan generated";
    els.runMeta.textContent = state.runId ? els.runMeta.textContent : "Idle";
    setStatus(state.runId ? "running" : "idle");
  }
  updateWorkflowReadiness();
}

function hasTellMeTaskSpec() {
  const spec = state.tellme.current?.tracefix_task_spec;
  return Boolean(state.tellme.current && spec && Object.keys(spec).length && state.tellme.current.status !== "not_answerable");
}

function hasTracefixWorkspace() {
  return Boolean(
    state.runId
    || state.artifacts?.workspace
    || state.artifacts?.cityosPlan
    || state.artifacts?.files?.length
  );
}

function hasCityOSReadyState() {
  return Boolean(
    state.synth.selected?.ready
    || state.synth.result?.manifestPath
    || state.synth.result?.apps?.length
  );
}

function markWorkflowSeen(workflow) {
  if (workflow) state.workflowSeen[workflow] = true;
}

function updateWorkflowReadiness() {
  const workflows = ["tellme", "planner", "synth"];
  const readyByWorkflow = {
    tellme: Boolean(state.tellme.current),
    planner: hasTellMeTaskSpec() || hasTracefixWorkspace(),
    synth: hasTracefixWorkspace() || hasCityOSReadyState(),
  };

  workflows.forEach((workflow) => {
    const isReady = Boolean(readyByWorkflow[workflow]);
    const justBecameReady = isReady && !state.workflowReady[workflow];
    if (!isReady) {
      state.workflowSeen[workflow] = false;
    } else if (workflow === state.workflow) {
      state.workflowSeen[workflow] = true;
    } else if (justBecameReady) {
      state.workflowSeen[workflow] = false;
    }
  });
  state.workflowReady = readyByWorkflow;

  const glowWorkflow = workflows.find((workflow) => (
    readyByWorkflow[workflow]
    && workflow !== state.workflow
    && !state.workflowSeen[workflow]
  ));

  document.querySelectorAll("[data-workflow]").forEach((button) => {
    const shouldGlow = button.dataset.workflow === glowWorkflow;
    button.classList.toggle("ready", shouldGlow);
    if (shouldGlow) {
      button.title = "Ready to review";
    } else {
      button.removeAttribute("title");
    }
  });
}

function updateKeyFields() {
  const provider = els.provider.value;
  document.querySelectorAll("[data-key-field]").forEach((field) => {
    field.classList.toggle("hidden", field.dataset.keyField !== provider);
  });
}

function updateModelSuggestions() {
  const options = modelOptions[els.provider.value] || [];
  els.modelSuggestions.innerHTML = options
    .map((model) => `<option value="${escapeHtml(model)}"></option>`)
    .join("");
}

async function loadModelOptions() {
  try {
    const data = await getJson("/api/model-options");
    if (data.models) {
      modelOptions = { ...modelOptions, ...data.models };
      updateModelSuggestions();
    }
  } catch {
    updateModelSuggestions();
  }
}

async function loadUiInfo() {
  if (!els.uiBuildStamp) return;
  try {
    const info = await getJson("/api/ui-info");
    els.uiBuildStamp.textContent = `${info.build || "unknown-ui-build"} | ${info.static_dir || "unknown static dir"}`;
    els.uiBuildStamp.title = `Repo: ${info.repo_root || "unknown"}`;
  } catch (error) {
    els.uiBuildStamp.textContent = "UI build info unavailable";
    els.uiBuildStamp.title = error.message;
  }
}

async function loadTellMeConfig() {
  try {
    const response = await getJson("/api/tellme/config");
    const envDetected = response.api_key_detected === true
      || response.data?.openai_api_key_detected === true
      || response.data?.tellme_api_key_detected === true;
    state.tellme.envKeyDetected = envDetected;
    state.tellme.apiKeyDetected = envDetected;
    state.tellme.model = response.model || response.data?.model || "gpt-4.1-mini";
    els.tellmeModel.value = state.tellme.model;
  } catch {
    state.tellme.envKeyDetected = false;
    state.tellme.apiKeyDetected = false;
  }
  updateTellMeModeFields();
  updateTellMeTracefixFields();
}

async function loadJsonContext() {
  try {
    const response = await getJson("/api/context/current");
    state.tellme.context = response.data || null;
    renderJsonContextStatus();
  } catch (error) {
    state.tellme.context = null;
    renderJsonContextStatus(error.message);
  }
}

async function uploadJsonContext() {
  const file = els.jsonContextFile?.files?.[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".json")) {
    renderJsonContextStatus("Please choose a .json file.");
    showToast("JSON context upload requires a .json file");
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  if (els.jsonContextStatus) els.jsonContextStatus.textContent = `Uploading: ${file.name}`;
  try {
    const response = await fetch("/api/context/upload", {
      method: "POST",
      body: form,
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.errors?.join("; ") || `Upload failed: ${response.status}`);
    }
    state.tellme.context = data.data || null;
    renderJsonContextStatus();
    showToast(`Loaded JSON context: ${state.tellme.context?.filename || file.name}`);
  } catch (error) {
    renderJsonContextStatus(error.message);
    showToast(error.message);
  } finally {
    if (els.jsonContextFile) els.jsonContextFile.value = "";
  }
}

function renderJsonContextStatus(errorText = "") {
  if (!els.jsonContextStatus) return;
  const context = state.tellme.context || {};
  els.jsonContextStatus.classList.toggle("loaded", context.loaded === true && !errorText);
  els.jsonContextStatus.classList.toggle("error", Boolean(errorText));
  if (errorText) {
    els.jsonContextStatus.textContent = errorText;
  } else if (context.loaded) {
    const size = context.size_bytes ? ` (${formatNumber(context.size_bytes)} bytes)` : "";
    els.jsonContextStatus.textContent = `Loaded: ${context.filename || "uploaded.json"}${size}`;
  } else {
    els.jsonContextStatus.textContent = "No JSON context loaded";
  }
}

function updateTellMeModeFields() {
  const isLlm = els.tellmeMode.value === "llm";
  els.tellmeModel.disabled = !isLlm;
  els.tellmeKeyRow.classList.toggle("hidden", !isLlm);

  const typedKey = (els.tellmeApiKey.value || "").trim();
  let statusText, statusClass;
  if (typedKey) {
    statusText = "API key ready";
    statusClass = "detected";
  } else if (state.tellme.envKeyDetected) {
    statusText = "Environment API key detected";
    statusClass = "detected";
  } else {
    statusText = "No API key detected";
    statusClass = "missing";
  }
  els.tellmeKeyStatus.textContent = statusText;
  els.tellmeKeyStatus.className = `api-key-status ${statusClass}`;
}

function providerKeyElement(provider) {
  if (provider === "anthropic") return els.anthropicKey;
  if (provider === "openrouter") return els.openrouterKey;
  if (provider === "openai") return els.openaiKey;
  return null;
}

function readProviderKey(provider) {
  const field = providerKeyElement(provider);
  return field ? (field.value || "").trim() : "";
}

function writeProviderKey(provider, value) {
  const field = providerKeyElement(provider);
  if (field) field.value = value;
}

function selectTellMeTracefixProvider(provider) {
  els.provider.value = provider;
  const nextModel = modelDefaults[provider] || (modelOptions[provider] || [])[0] || els.tellmeTracefixModel.value;
  els.model.value = nextModel;
  els.tellmeTracefixModel.value = nextModel;
  els.tellmeTracefixApiKey.value = readProviderKey(provider);
  els.tellmeTracefixOllamaUrl.value = els.ollamaUrl.value;
  updateModelSuggestions();
  updateKeyFields();
  updateTellMeTracefixFields();
}

function syncTellMeTracefixFromPlanner() {
  const provider = els.provider.value;
  els.tellmeTracefixProvider.value = provider;
  els.tellmeTracefixModel.value = els.model.value || modelDefaults[provider] || "";
  els.tellmeTracefixOllamaUrl.value = els.ollamaUrl.value;
  els.tellmeTracefixApiKey.value = readProviderKey(provider);
  updateTellMeTracefixFields();
}

function syncPlannerFromTellMeTracefix() {
  const provider = els.tellmeTracefixProvider.value;
  els.provider.value = provider;
  els.model.value = els.tellmeTracefixModel.value.trim() || modelDefaults[provider] || els.model.value;
  if (provider === "ollama") {
    els.ollamaUrl.value = els.tellmeTracefixOllamaUrl.value;
  } else {
    writeProviderKey(provider, els.tellmeTracefixApiKey.value.trim());
  }
  updateModelSuggestions();
  updateKeyFields();
  updateTellMeTracefixFields();
}

function updateTellMeTracefixFields() {
  const provider = els.tellmeTracefixProvider.value;
  const isOllama = provider === "ollama";
  els.tellmeTracefixKeyLabel.classList.toggle("hidden", isOllama);
  els.tellmeTracefixOllamaLabel.classList.toggle("hidden", !isOllama);

  const typedTracefixKey = (els.tellmeTracefixApiKey.value || "").trim();
  const plannerKey = readProviderKey(provider);
  const tellmeKey = (els.tellmeApiKey.value || "").trim();
  let statusText = "No TraceFix API key detected";
  let statusClass = "missing";

  if (isOllama) {
    const ollamaReady = (els.tellmeTracefixOllamaUrl.value || "").trim();
    statusText = ollamaReady ? "Ollama URL ready" : "No Ollama URL configured";
    statusClass = ollamaReady ? "detected" : "missing";
  } else if (typedTracefixKey || plannerKey) {
    statusText = "TraceFix API key ready";
    statusClass = "detected";
  } else if (provider === "openai" && tellmeKey) {
    statusText = "Using TeLLMe API key for TraceFix";
    statusClass = "detected";
  } else if (provider === "openai" && state.tellme.envKeyDetected) {
    statusText = "Environment API key detected";
    statusClass = "detected";
  }

  els.tellmeTracefixKeyStatus.textContent = statusText;
  els.tellmeTracefixKeyStatus.className = `api-key-status ${statusClass}`;
}

async function loadTellMeCurrent() {
  try {
    const response = await getJson("/api/tellme/current");
    if (response.api_key_detected === true) {
      state.tellme.envKeyDetected = true;
      state.tellme.apiKeyDetected = true;
    }
    if (response.model) state.tellme.model = response.model;
    if (response.ok && response.data) {
      state.tellme.current = response.data;
      state.tellme.runId = response.run_id;
      renderTellMe(response.data, response.errors, response.warnings);
    } else {
      renderTellMe(null, response.errors, response.warnings);
    }
  } catch (error) {
    renderTellMe(null, [error.message], []);
  }
  updateTellMeModeFields();
  updateTellMeTracefixFields();
}

async function processTellMeQuery() {
  const query = els.tellmeQuery.value.trim();
  if (!query) {
    showToast("Enter a smart-room request");
    return null;
  }
  els.tellmeProcess.disabled = true;
  els.tellmeRunAll.disabled = true;
  els.tellmeRunStatus.textContent = "Processing";
  els.tellmeMessages.innerHTML = `<div class="message-card info">TeLLMe is analyzing route, privacy, and task decomposition...</div>`;
  try {
    const isLlm = els.tellmeMode.value === "llm";
    const response = await postJson("/api/tellme/query", {
      query,
      space_id: els.tellmeSpaceId.value.trim() || "smart_room_1",
      timestamp: els.tellmeTimestamp.value.trim() || null,
      mode: els.tellmeMode.value,
      model: els.tellmeModel.value.trim() || state.tellme.model,
      llm_timeout_seconds: TELLME_LLM_TIMEOUT_SECONDS,
      api_key: isLlm ? (els.tellmeApiKey.value.trim() || null) : null,
    });
    state.tellme.apiKeyDetected = response.api_key_detected === true;
    state.tellme.model = response.model || state.tellme.model;
    state.tellme.current = response.data;
    state.tellme.runId = response.run_id;
    renderTellMe(response.data, response.errors, response.warnings);
    showToast("TeLLMe task spec generated");
    return response.data;
  } catch (error) {
    renderTellMe(null, [error.message], []);
    return null;
  } finally {
    els.tellmeProcess.disabled = false;
    els.tellmeRunAll.disabled = false;
    updateTellMeModeFields();
    updateTellMeTracefixFields();
  }
}

async function processTellMeAndTraceFix() {
  const data = await processTellMeQuery();
  if (!data) return;
  if (data.status === "not_answerable") {
    showToast("TeLLMe blocked this request");
    return;
  }
  await startTraceFixFromTellMe();
}

function appendTellMeMessage(kind, text) {
  const safeKind = ["success", "warning", "error", "info"].includes(kind) ? kind : "info";
  els.tellmeMessages.insertAdjacentHTML("beforeend", `<div class="message-card ${safeKind}">${escapeHtml(text)}</div>`);
}

function pipelineWebDataUrl() {
  const fromTellMe = els.tellmeWebDataUrl?.value?.trim() || "";
  const fromSynth = els.synthWebDataUrl?.value?.trim() || "";
  const url = fromTellMe || fromSynth || state.synth.webDataUrl || "https://smartroom-mirror.vercel.app/api/v1";
  if (els.tellmeWebDataUrl) els.tellmeWebDataUrl.value = url;
  if (els.synthWebDataUrl) els.synthWebDataUrl.value = url;
  state.synth.webDataUrl = url;
  return url;
}

function pipelineRawDataJson() {
  const raw = (els.tellmeRawDataJson?.value || els.synthRawDataJson?.value || "").trim();
  if (els.tellmeRawDataJson && els.tellmeRawDataJson.value.trim() !== raw) els.tellmeRawDataJson.value = raw;
  if (els.synthRawDataJson && els.synthRawDataJson.value.trim() !== raw) els.synthRawDataJson.value = raw;
  return raw;
}
async function refreshTellMeCurrentFromServer() {
  const response = await getJson("/api/tellme/current");
  if (response.ok && response.data) {
    state.tellme.current = response.data;
    state.tellme.runId = response.run_id;
    renderTellMe(response.data, response.errors, response.warnings);
  }
  syncTellMeAnswerButton();
  return response.data || null;
}

function hasTellMeAnswer(data = state.tellme.current) {
  if (!data) return false;
  const answer = data.web_data_answer || data.answer_packet?.answer || null;
  return Boolean(
    data.chat_answer
    || data.answer_summary
    || data.web_data_answer_path
    || answer?.chatAnswer
    || answer?.chat_answer
    || answer?.text
    || answer?.answer
  );
}

function syncTellMeAnswerButton() {
  if (els.synthViewTellMeAnswer) {
    els.synthViewTellMeAnswer.disabled = !hasTellMeAnswer();
  }
}

function setWorkflow(workflow) {
  state.workflow = workflow;
  markWorkflowSeen(workflow);
  document.querySelectorAll("[data-workflow]").forEach((button) => {
    button.classList.toggle("active", button.dataset.workflow === workflow);
  });
  updateWorkflow();
}

async function viewTellMeAnswer() {
  try {
    await refreshTellMeCurrentFromServer();
  } catch (error) {
    showToast(error.message);
  }
  if (!hasTellMeAnswer()) {
    showToast("No TeLLMe answer yet");
    syncTellMeAnswerButton();
    return;
  }
  setWorkflow("tellme");
  window.requestAnimationFrame(() => {
    const target = els.tellmeChatAnswer || els.tellmeAnswer;
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  showToast("Showing TeLLMe answer");
}
async function processTellMeFullPipeline() {
  const originalText = els.tellmeRunAll.textContent;
  els.tellmeRunAll.textContent = "Running Full Pipeline...";
  els.tellmeRunAll.disabled = true;
  els.tellmeProcess.disabled = true;
  els.tellmeToTracefix.disabled = true;
  try {
    const data = await processTellMeQuery();
    els.tellmeRunAll.disabled = true;
    els.tellmeProcess.disabled = true;
    els.tellmeToTracefix.disabled = true;
    if (!data) return;
    if (data.status === "not_answerable") {
      showToast("TeLLMe blocked this request");
      return;
    }

    appendTellMeMessage("info", "TraceFix verification is running from the TeLLMe task spec...");
    const tracefixRun = await startTraceFixFromTellMe({ waitForCompletion: true, stayOnTellMe: true });
    const workspace = tracefixRun?.artifacts?.workspace || state.artifacts?.workspace || currentWorkspacePath();
    if (!workspace) throw new Error("TraceFix completed but did not return a workspace for CityOS synthesis.");

    appendTellMeMessage("info", "CityOS synthesis is generating app bundles for the verified workspace...");
    state.synth.workspaceType = "custom";
    if (els.synthWorkspacePath) els.synthWorkspacePath.value = workspace;
    pipelineWebDataUrl();
    await loadSynthConfig({ preferCurrent: true });
    if (els.synthWorkspacePath) els.synthWorkspacePath.value = workspace;
    await loadSynthWorkspace(workspace);

    const cityosResponse = await postJson("/api/cityos/synthesize", {
      workspace,
      cityosRoot: els.synthCityOSRoot?.value || state.synth.cityosRoot || "",
      appsDir: els.synthOutputDir?.value || state.synth.appsDir || "",
      packageName: els.synthPackageName?.value || "",
      overwrite: els.synthOverwrite?.checked || false,
    });
    const cityosResult = cityosResponse.data || cityosResponse;
    state.synth.result = cityosResult;
    state.synth.selected = cityosResult.summary || state.synth.selected;
    if (cityosResult.summary) renderSynthSummary(cityosResult.summary);
    renderSynthArtifacts(cityosResult);

    appendTellMeMessage("info", "Smartroom web-data apps are loading the selected data and producing the final answer...");
    const rawDataJson = pipelineRawDataJson();
    const webResult = await postJson("/api/synth/run-web-data", {
      manifestPath: cityosResult.manifestPath,
      sourceUrl: pipelineWebDataUrl(),
      sourceMode: rawDataJson ? "raw-json" : "auto",
      rawDataJson,
      timeoutSeconds: 30,
      question: state.tellme.current?.query || els.tellmeQuery.value.trim(),
    });
    state.synth.webDataResult = webResult;
    await refreshTellMeCurrentFromServer();
    appendTellMeMessage(webResult.ok ? "success" : "warning", webResult.ok
      ? "Full pipeline complete. The Answer Summary now contains the requested smartroom result."
      : "Full pipeline finished, but one or more generated apps reported errors. Check the CityOS Synthesizer output.");
    showToast(webResult.ok ? "Full pipeline complete" : "Pipeline finished with app errors");
  } catch (error) {
    appendTellMeMessage("error", error.message);
    showToast(error.message);
  } finally {
    els.tellmeRunAll.textContent = originalText;
    els.tellmeRunAll.disabled = false;
    els.tellmeProcess.disabled = false;
    els.tellmeToTracefix.disabled = !state.tellme.current || state.tellme.current.status === "not_answerable";
    updateWorkflowReadiness();
  }
}

function formatCameraNameForAnswer(name) {
  return String(name || "camera").replace(/cam(\d+)/i, "cam $1");
}

function personCountLabel(value) {
  const count = Number(value);
  if (!Number.isFinite(count)) return `${value} people`;
  return `${count} ${count === 1 ? "person" : "people"}`;
}

function activityPhrase(labels) {
  const cleaned = (labels || []).filter(Boolean).map((item) => String(item));
  if (!cleaned.length) return "the requested activities";
  if (cleaned.length === 1) return cleaned[0];
  if (cleaned.length === 2) return `${cleaned[0]} and ${cleaned[1]}`;
  return `${cleaned.slice(0, -1).join(", ")}, and ${cleaned[cleaned.length - 1]}`;
}

function answerDateLead(answer) {
  const requested = answer?.selection?.requestedDateLabel || "";
  return requested ? `For ${requested}, ` : "";
}

function stripTechnicalAnswerDetails(text, options = {}) {
  let cleaned = String(text || "").trim();
  if (!cleaned) return "";
  cleaned = cleaned.replace(/\s+This uses matching track\/person IDs across the activity and pose endpoints\./g, "");
  cleaned = cleaned.replace(/\s+Counts are per detected activity label\/track; ask for 'both'[^.]*\./g, "");
  cleaned = cleaned.replace(/\s+The API data did not include matching track\/person IDs[^.]*\./g, "");
  if (!options.keepBreakdown) cleaned = cleaned.replace(/\s+Camera breakdown:[^.]*\./g, "");
  if (!options.keepActivities) cleaned = cleaned.replace(/\s+Detected activities\/poses included[^.]*\./g, "");
  cleaned = cleaned.replace(/\s+\((?:day_|rec_)[^)]+\)/g, "");
  return cleaned.replace(/\s+/g, " ").trim();
}

function questionAsksForActivity(question) {
  const text = ` ${String(question || "").toLowerCase()} `;
  return [
    " activity ", " activities ", " action ", " actions ", " doing ", " pose ", " poses ",
    " standing ", " talking ", " sitting ", " walking ", " clapping ", " moving ", " typing "
  ].some((token) => text.includes(token));
}

function conciseRequestedActivityAnswer(answer) {
  const requested = Array.isArray(answer?.requestedActivities) ? answer.requestedActivities : [];
  const lead = answerDateLead(answer);
  const combination = answer?.requestedActivityCombination || null;
  if (combination?.exact) {
    const labels = Array.isArray(combination.labels) ? combination.labels : requested;
    return `${lead}${personCountLabel(combination.count || 0)} were both ${activityPhrase(labels)}.`;
  }
  if (requested.length) {
    const counts = answer?.requestedActivityCounts || answer?.activityCounts || {};
    const pieces = requested.map((label) => `${label}: ${personCountLabel(counts[label] || 0)}`);
    return `${lead}${pieces.join(", ")}.`;
  }
  return stripTechnicalAnswerDetails(answer?.requestedActivityAnswer || "", { keepActivities: true });
}

function conciseOccupancyAnswer(answer) {
  const cameras = Array.isArray(answer?.cameras) ? answer.cameras : [];
  const peaks = [];
  const latestParts = [];
  cameras.forEach((camera) => {
    if (camera.peakPeople !== null && camera.peakPeople !== undefined) {
      const peak = Number(camera.peakPeople);
      if (Number.isFinite(peak)) peaks.push(peak);
    }
    if (camera.lastPeople !== null && camera.lastPeople !== undefined) {
      latestParts.push(`${formatCameraNameForAnswer(camera.camera)}: ${personCountLabel(camera.lastPeople)}`);
    }
  });
  const lead = answerDateLead(answer);
  const sentences = [];
  if (peaks.length) {
    sentences.push(`${lead}the peak observed occupancy was ${personCountLabel(Math.max(...peaks))} overall.`);
  }
  if (latestParts.length) {
    sentences.push(`The latest observed counts were ${latestParts.join("; ")}.`);
  }
  return sentences.join(" ");
}

function conciseActivityOverviewAnswer(answer) {
  const cameras = Array.isArray(answer?.cameras) ? answer.cameras : [];
  const activityParts = [];
  cameras.forEach((camera) => {
    const activities = camera.activities?.length ? camera.activities : camera.actions;
    if (activities?.length) {
      activityParts.push(`${formatCameraNameForAnswer(camera.camera)}: ${activities.slice(0, 6).join(", ")}`);
    }
  });
  if (!activityParts.length) return "";
  return `${answerDateLead(answer)}the observed activities/poses were ${activityParts.join("; ")}.`;
}

function renderTellMeChatAnswer(data) {
  if (!data) return "No answer yet. Run the full pipeline to generate a data-backed response.";
  const answer = data.web_data_answer || data.answer_packet?.answer || null;
  const fallback = data.chat_answer || answer?.chatAnswer || answer?.chat_answer || "";
  if (!answer) return fallback || "No data-backed result is available yet. Run the web data apps after synthesis to generate the answer.";

  const question = data.query || answer.question || "";
  const requestedActivityText = conciseRequestedActivityAnswer(answer);
  if (requestedActivityText) return requestedActivityText;

  if (questionAsksForActivity(question)) {
    const activityAnswer = conciseActivityOverviewAnswer(answer);
    if (activityAnswer) return activityAnswer;
  }

  const occupancyAnswer = conciseOccupancyAnswer(answer);
  if (occupancyAnswer) return occupancyAnswer;

  const cleanedFallback = stripTechnicalAnswerDetails(fallback, { keepActivities: questionAsksForActivity(question) });
  if (cleanedFallback) return cleanedFallback;
  return "I loaded the selected data, but there was not enough relevant information to answer the question yet.";
}
function renderTellMeAnswerSummary(data) {
  if (!data) return "No answer summary yet.";
  const lines = [];
  const answer = data.web_data_answer || data.answer_packet?.answer || null;
  const answerText = data.answer_summary || answer?.text || answer?.answer || "";
  if (answerText) {
    lines.push("Answer Summary");
    lines.push(answerText);
  } else {
    const route = data.route_decision || {};
    const spec = data.tracefix_task_spec || {};
    const goal = spec.application_goal || {};
    lines.push("Answer Summary");
    lines.push(route.requires_tracefix === true || data.status === "needs_tracefix"
      ? "Ready for TraceFix verification. Run the generated apps against the selected web or uploaded JSON data to produce the final data-backed answer."
      : "TeLLMe produced a privacy-bounded answer plan.");
    if (data.query) lines.push(`Request: ${data.query}`);
    if (goal.goal_type || goal.user_intent) {
      lines.push(`Goal: ${goal.goal_type || goal.user_intent}`);
    }
  }

  if (data.web_data_snapshot_summary) {
    const snapshot = data.web_data_snapshot_summary;
    lines.push("");
    lines.push("Data Snapshot");
    if (snapshot.selectedRecording || snapshot.selectedDay) {
      lines.push(`Recording: ${[snapshot.selectedDay, snapshot.selectedRecording].filter(Boolean).join(" / ")}`);
    }
    if (snapshot.requestedDateLabel) lines.push(`Requested date: ${snapshot.requestedDateLabel}`);
    if (snapshot.selectionReason) lines.push(`Selection: ${snapshot.selectionReason}`);
    if (Array.isArray(snapshot.cameras) && snapshot.cameras.length) {
      lines.push(`Cameras: ${snapshot.cameras.join(", ")}`);
    }
    if (snapshot.recordingCount !== undefined) lines.push(`Recordings available: ${snapshot.recordingCount}`);
    if (snapshot.errors) lines.push(`Data errors: ${snapshot.errors}`);
  }

  if (answer?.requestedActivityAnswer) {
    lines.push("");
    lines.push("Requested Activity Counts");
    lines.push(answer.requestedActivityAnswer);
  }
  if (answer?.activityCounts && Object.keys(answer.activityCounts).length) {
    lines.push("");
    lines.push("Activity Count Totals");
    Object.entries(answer.activityCounts).forEach(([label, count]) => lines.push(`- ${label}: ${count}`));
  }

  if (answer?.cameras?.length) {
    lines.push("");
    lines.push("Camera Results");
    answer.cameras.forEach((camera) => {
      const details = [];
      if (camera.peakPeople !== null && camera.peakPeople !== undefined) details.push(`peak ${camera.peakPeople}`);
      if (camera.lastPeople !== null && camera.lastPeople !== undefined) details.push(`latest ${camera.lastPeople}`);
      const activities = camera.activities?.length ? camera.activities : camera.actions;
      if (activities?.length) details.push(`activities/poses ${activities.join(", ")}`);
      if (camera.activityCounts && Object.keys(camera.activityCounts).length) details.push(`activity counts ${Object.entries(camera.activityCounts).map(([label, count]) => `${label}: ${count}`).join(", ")}`);
      if (camera.pose?.available) details.push(`pose models ${(camera.pose.models || []).map((item) => item.model).filter(Boolean).join(", ")}`);
      if (camera.endpoints && Object.keys(camera.endpoints).length) details.push(`model endpoints ${Object.keys(camera.endpoints).join(", ")}`);
      if (camera.framePath) details.push(`frame ${camera.framePath}`);
      lines.push(`- ${camera.camera || "camera"}: ${details.join("; ") || "no summarized result"}`);
    });
  }

  if (data.web_data_answer_path) {
    lines.push("");
    lines.push(`Answer file: ${data.web_data_answer_path}`);
  }
  if (data.web_data_payload_path) {
    lines.push(`Payload file: ${data.web_data_payload_path}`);
  }
  return lines.join("\n");
}
function renderTellMe(data, errors = [], warnings = []) {
  const route = data?.route_decision || {};
  const privacy = data?.privacy_guardrail || {};
  const spec = data?.tracefix_task_spec || {};
  const harnesses = Array.isArray(spec.candidate_harnesses) ? spec.candidate_harnesses : [];
  els.tellmeRoute.textContent = route.route || "Awaiting request";
  els.tellmeRationale.textContent = route.rationale || "TeLLMe will classify the request before TraceFix design.";
  els.tellmePrivacy.textContent = privacy.status || "Not evaluated";
  els.tellmePrivacy.className = privacy.status === "passed" ? "status-text pass" : privacy.status === "blocked" ? "status-text fail" : "";
  els.tellmePrivacyScope.textContent = privacy.privacy_scope || "CityOS structured context only";
  els.tellmeTaskCount.textContent = data ? "1" : "0";
  els.tellmeHarnessCount.textContent = `${harnesses.length} candidate harness${harnesses.length === 1 ? "" : "es"}`;
  els.tellmeRunStatus.textContent = data?.status || "Idle";
  els.tellmeRunId.textContent = data?.query_id || "No run yet";
  els.tellmeIntent.textContent = data ? JSON.stringify(data.intent_decomposition || data.execution_brief || {}, null, 2) : "No intent decomposition yet.";
  const handoffText = data?.tracefix_handoff_text || "";
  els.tellmeTaskSpec.textContent = handoffText || (data ? "No TraceFix handoff is available for this request." : "No TraceFix handoff yet.");
  if (els.tellmeHandoffMeta) {
    els.tellmeHandoffMeta.textContent = handoffText
      ? `Exact payload · ${handoffText.length.toLocaleString()} chars`
      : "No handoff";
  }
  els.tellmeAnswer.textContent = renderTellMeAnswerSummary(data);
  if (els.tellmeChatAnswer) els.tellmeChatAnswer.textContent = renderTellMeChatAnswer(data);
  syncTellMeAnswerButton();
  els.tellmeToTracefix.disabled = !data || !Object.keys(spec).length || data.status === "not_answerable";

  const messages = [
    ...(errors || []).map((text) => ({ kind: "error", text })),
    ...(warnings || []).map((text) => ({ kind: "warning", text })),
  ];
  if (data && !messages.length) {
    messages.push({
      kind: privacy.status === "blocked" ? "error" : "success",
      text: privacy.status === "blocked"
        ? "Privacy guardrail blocked this request before TraceFix."
        : "Typed TeLLMe task is ready for TraceFix verification.",
    });
  }
  els.tellmeMessages.innerHTML = messages
    .map((item) => `<div class="message-card ${item.kind}">${escapeHtml(item.text)}</div>`)
    .join("");
  updateWorkflowReadiness();
}

function tracefixPayloadKeys(provider) {
  const firstPageKey = (els.tellmeTracefixApiKey.value || "").trim();
  const tellmeKey = (els.tellmeApiKey.value || "").trim();
  return {
    openaiKey: (els.openaiKey.value || (provider === "openai" ? firstPageKey || tellmeKey : "")).trim(),
    anthropicKey: (els.anthropicKey.value || (provider === "anthropic" ? firstPageKey : "")).trim(),
    openrouterKey: (els.openrouterKey.value || (provider === "openrouter" ? firstPageKey : "")).trim(),
  };
}

function traceFixProviderPayload() {
  syncPlannerFromTellMeTracefix();
  const provider = els.provider.value;
  return {
    provider,
    model: normalizeModelForPayload(provider, els.model.value),
    ...tracefixPayloadKeys(provider),
    ollamaUrl: els.ollamaUrl.value,
    opencodeBin: els.opencodeBin.value,
    timeout: Number(els.timeout.value || 1800),
    verbose: true,
  };
}

async function startTraceFixFromTellMe(options = {}) {
  const waitForCompletion = options.waitForCompletion === true;
  const stayOnTellMe = options.stayOnTellMe === true;
  resetRunUi();
  try {
    const response = await postJson("/api/tracefix/from-tellme", traceFixProviderPayload());
    const run = response.data;
    state.runId = run.id;
    state.taskMode = "custom";
    state.runMode = "design";
    if (!stayOnTellMe) {
      state.workflow = "planner";
    }
    markWorkflowSeen("planner");
    document.querySelectorAll("[data-workflow]").forEach((button) => {
      button.classList.toggle("active", button.dataset.workflow === state.workflow);
    });
    document.querySelectorAll("[data-task-mode]").forEach((button) => {
      button.classList.toggle("active", button.dataset.taskMode === "custom");
    });
    els.benchmarkField.classList.add("hidden");
    els.customTaskField.classList.remove("hidden");
    // Store the real task spec text returned by the server so that any
    // subsequent "Generate Verified Plan" press sends the actual spec,
    // not just the human-readable placeholder.
    const actualTaskText = run.tellme_task_text || "";
    state.tellme.taskText = actualTaskText;
    els.customTask.value = actualTaskText || "Loaded automatically from the current TeLLMe task spec.";
    if (!stayOnTellMe) {
      updateWorkflow();
    } else {
      updateWorkflowReadiness();
    }
    els.startRun.disabled = true;
    els.stopRun.disabled = false;
    els.runTitle.textContent = "Designing TeLLMe application";
    els.runMeta.textContent = `${els.provider.value} / ${els.model.value}`;
    setStatus("running");
    const completion = waitForCompletion
      ? { settled: false, promise: null, resolve: null, reject: null }
      : null;
    if (completion) {
      completion.promise = new Promise((resolve, reject) => {
        completion.resolve = resolve;
        completion.reject = reject;
      });
    }
    connectEvents(run.id, completion);
    return completion ? await completion.promise : run;
  } catch (error) {
    setStatus("failed");
    appendTimeline("error", error.message);
    renderOutput();
    showToast(error.message);
    if (waitForCompletion) throw error;
    return null;
  }
}

async function loadSynthConfig(options = {}) {
  // Sync benchmark field visibility with current type
  const isBenchmark = state.synth.workspaceType === "benchmark";
  if (els.synthBenchmarkField) {
    els.synthBenchmarkField.classList.toggle("hidden", !isBenchmark);
  }

  try {
    const benchmark = isBenchmark ? els.synthBenchmarkSelect.value : "";
    const params = new URLSearchParams();
    if (benchmark) params.set("benchmark", benchmark);
    params.set("workspaceType", state.synth.workspaceType);
    const data = await getJson(`/api/synth/config?${params}`);
    state.synth.workspaces = data.workspaces || [];
    state.synth.cityosRoot = data.cityosRoot || "";
    state.synth.appsDir = data.appsDir || "";
    state.synth.webDataUrl = data.webDataUrl || state.synth.webDataUrl;
    if (els.synthCityOSRoot && !els.synthCityOSRoot.value) els.synthCityOSRoot.value = state.synth.cityosRoot;
    if (els.synthWebDataUrl && !els.synthWebDataUrl.value) els.synthWebDataUrl.value = state.synth.webDataUrl;
    if (els.tellmeWebDataUrl && !els.tellmeWebDataUrl.value) els.tellmeWebDataUrl.value = state.synth.webDataUrl;
    renderSynthWorkspaceOptions();
    const workspacePaths = new Set(state.synth.workspaces.map((workspace) => workspace.path));
    let current = options.preferCurrent === false ? "" : (els.synthWorkspacePath.value || state.artifacts.workspace || "");
    if (!current) {
      // Try to restore the last verified TraceFix workspace from the server's
      // cross-stage handoff (current.json), so the CityOS tab auto-selects after
      // a TraceFix run even if the user reloaded the page.
      try {
        const cityosCurrent = await getJson("/api/cityos/current");
        current = cityosCurrent?.data?.workspace?.path || "";
      } catch (_) {
        // Non-fatal — fall through to first available workspace.
      }
    }
    if (!current || !workspacePaths.has(current)) {
      current = state.synth.workspaces[0]?.path || current;
    }
    if (current) {
      els.synthWorkspacePath.value = current;
      els.synthWorkspaceSelect.value = current;
      await loadSynthWorkspace(current);
    } else {
      renderSynthSummary(null);
    }
  } catch (error) {
    els.synthStatus.textContent = error.message;
    els.synthOutput.textContent = error.stack || error.message;
  }
}

function renderSynthWorkspaceOptions() {
  els.synthWorkspaceSelect.innerHTML = state.synth.workspaces.length
    ? state.synth.workspaces
        .map((workspace) => {
          const VS_LABELS = { verified: "verified", verified_no_summary: "verified", unknown: "pending", verification_incomplete: "incomplete" };
          const readiness = workspace.ready ? "ready" : workspace.hasPlan ? (VS_LABELS[workspace.verificationStatus] || workspace.verificationStatus || "pending") : "missing plan";
          const modified = workspace.lastModified
            ? new Date(workspace.lastModified * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
            : "";
          const typeTag = workspace.workspaceType === "custom" ? "custom" : null;
          const label = [workspace.name, typeTag, readiness, modified].filter(Boolean).join(" · ");
          return `<option value="${escapeHtml(workspace.path)}">${escapeHtml(label)}</option>`;
        })
        .join("")
    : `<option value="">No workspaces found</option>`;
}

async function loadSynthWorkspace(workspacePath) {
  if (!workspacePath) {
    renderSynthSummary(null);
    return;
  }
  try {
    const data = await getJson(`/api/synth/workspace?workspace=${encodeURIComponent(workspacePath)}`);
    state.synth.selected = data.workspace;
    renderSynthSummary(data.workspace);
  } catch (error) {
    state.synth.selected = null;
    els.synthStatus.textContent = error.message;
    els.synthChecklist.innerHTML = `<div class="check-item missing">Workspace check failed: ${escapeHtml(error.message)}</div>`;
  }
}

function renderSynthSummary(summary) {
  if (!summary) {
    els.synthStatus.textContent = "Select a verified workspace";
    if (els.synthWorkspaceTypeDisplay) els.synthWorkspaceTypeDisplay.textContent = "—";
    if (els.synthTaskTextDisplay) els.synthTaskTextDisplay.textContent = "—";
    els.synthWorkspaceDisplay.textContent = "None selected";
    els.synthPlanDisplay.textContent = "Unavailable";
    els.synthAgentsDisplay.textContent = "0";
    els.synthChannelsDisplay.textContent = "0";
    els.synthResourcesDisplay.textContent = "0";
    els.synthChecklist.innerHTML = "";
    updateWorkflowReadiness();
    return;
  }
  const isCustom = summary.workspaceType === "custom";
  els.synthStatus.textContent = summary.ready
    ? "Ready for CityOS synthesis"
    : `Not ready: ${({ verified_no_summary: "Verified (no TLC summary)", unknown: "pending verification", verification_incomplete: "incomplete" })[summary.verificationStatus] || summary.verificationStatus || "missing artifacts"}`;

  if (els.synthWorkspaceTypeDisplay) {
    els.synthWorkspaceTypeDisplay.textContent = isCustom ? "Custom TraceFix Task" : "Benchmark";
  }
  if (els.synthTaskTextDisplay) {
    const taskText = summary.taskText || summary.name || "";
    els.synthTaskTextDisplay.textContent = taskText || "—";
    els.synthTaskTextDisplay.title = taskText;
  }

  console.log(
    `[synth] workspace=${summary.name} type=${summary.workspaceType} ` +
    `ready=${summary.ready} path=${summary.path} ` +
    `outputDir=${summary.outputDir}`
  );

  els.synthWorkspaceDisplay.textContent = summary.name || summary.path;
  els.synthPlanDisplay.textContent = summary.hasPlan ? summary.planPath : "Missing spec/cityos_module_plan.json";
  els.synthOutputDir.value = state.synth.appsDir || summary.outputDir || "";
  els.synthAgentsDisplay.textContent = String((summary.agents || []).length);
  els.synthChannelsDisplay.textContent = String((summary.channels || []).length);
  els.synthResourcesDisplay.textContent = String((summary.resources || []).length);
  els.synthChecklist.innerHTML = (summary.requirements || [])
    .map((item) => `
      <div class="check-item ${item.exists ? "ok" : item.required ? "missing" : "optional"}">
        <span>${item.exists ? "OK" : item.required ? "Missing" : "Optional"}</span>
        <div>
          <strong>${escapeHtml(item.path)}</strong>
          <small>${escapeHtml(item.exists ? item.absolutePath : item.createdBy)}</small>
        </div>
      </div>
    `)
    .join("");
  updateWorkflowReadiness();
}

async function synthesizeCityOSArtifacts() {
  const workspacePath = els.synthWorkspacePath.value.trim();
  if (!workspacePath) {
    showToast("Choose a TraceFix workspace first");
    return;
  }
  els.synthGenerate.disabled = true;
  els.synthOutputStatus.textContent = "Generating...";
  renderSynthArtifacts(null);
  els.synthOutput.textContent = "Calling synthesize_cityos_apps(...) through the active runner backend...\n";
  try {
    const result = await postJson("/api/synth/synthesize", {
      workspace: workspacePath,
      packageName: els.synthPackageName.value,
      overwrite: els.synthOverwrite.checked,
      cityosRoot: els.synthCityOSRoot.value,
      appsDir: els.synthOutputDir.value,
    });
    state.synth.result = result;
    state.synth.selected = result.summary;
    renderSynthSummary(result.summary);
    els.synthOutputStatus.textContent = "Generated";
    renderSynthArtifacts(result);
    els.synthOutput.textContent = renderSynthResult(result);
    showToast("CityOS artifacts generated");
  } catch (error) {
    els.synthOutputStatus.textContent = "Failed";
    els.synthOutput.textContent += `\n${error.message}\n`;
    showToast(error.message);
  } finally {
    els.synthGenerate.disabled = false;
  }
}

async function buildCityOSArtifacts() {
  const manifestPath = state.synth.result?.manifestPath || els.synthManifestPath.textContent.trim();
  if (!manifestPath || manifestPath === "No synthesis yet") {
    showToast("Generate CityOS artifacts first");
    return;
  }
  els.synthBuildCityOS.disabled = true;
  els.synthOutputStatus.textContent = "Building in CityOS...";
  const prior = els.synthOutput.textContent || "";
  els.synthOutput.textContent = `${prior}${prior ? "\n\n" : ""}Running Docker builds from CityOS root...\n`;
  try {
    const result = await postJson("/api/synth/build-cityos", {
      manifestPath,
      cityosRoot: els.synthCityOSRoot.value,
      timeoutSeconds: 1800,
    });
    els.synthOutputStatus.textContent = result.ok ? "Built in CityOS" : "Build failed";
    els.synthOutput.textContent = `${els.synthOutput.textContent}\n${renderCityOSBuildResult(result)}`;
    showToast(result.ok ? "CityOS Docker build complete" : "CityOS build failed");
  } catch (error) {
    els.synthOutputStatus.textContent = "Build failed";
    els.synthOutput.textContent = `${els.synthOutput.textContent}\n${error.message}\n`;
    showToast(error.message);
  } finally {
    els.synthBuildCityOS.disabled = !(state.synth.result?.apps || []).length;
  }
}

async function runWebDataApps() {
  const manifestPath = state.synth.result?.manifestPath || els.synthManifestPath.textContent.trim();
  if (!manifestPath || manifestPath === "No synthesis yet") {
    showToast("Generate CityOS artifacts first");
    return;
  }
  const sourceUrl = els.synthWebDataUrl?.value?.trim() || state.synth.webDataUrl || "https://smartroom-mirror.vercel.app/api/v1";
  const rawDataJson = pipelineRawDataJson();
  els.synthRunWebData.disabled = true;
  els.synthOutputStatus.textContent = "Running web data apps...";
  const prior = els.synthOutput.textContent || "";
  els.synthOutput.textContent = `${prior}${prior ? "\n\n" : ""}Loading web data and feeding generated apps...\n`;
  try {
    const result = await postJson("/api/synth/run-web-data", {
      manifestPath,
      sourceUrl,
      sourceMode: rawDataJson ? "raw-json" : "auto",
      rawDataJson,
      timeoutSeconds: 30,
      question: state.tellme.current?.query || els.tellmeQuery.value.trim(),
    });
    els.synthOutputStatus.textContent = result.ok ? "Web data run complete" : "Web data run failed";
    els.synthOutput.textContent = `${els.synthOutput.textContent}\n${renderWebDataRunResult(result)}`;
    await refreshTellMeCurrentFromServer();
    showToast(result.ok ? "Web data apps completed" : "Web data apps reported errors");
  } catch (error) {
    els.synthOutputStatus.textContent = "Web data run failed";
    els.synthOutput.textContent = `${els.synthOutput.textContent}\n${error.message}\n`;
    showToast(error.message);
  } finally {
    els.synthRunWebData.disabled = !(state.synth.result?.apps || []).length;
  }
}

function renderWebDataRunResult(result) {
  const lines = [];
  lines.push("Web Data App Run");
  lines.push(`Source: ${result.sourceUrl || "unknown"}`);
  if (result.sourceKind) lines.push(`Kind: ${result.sourceKind}`);
  lines.push(`Manifest: ${result.manifestPath || "unknown"}`);
  lines.push(`Output: ${result.outputRoot || "unknown"}`);
  if (result.question) lines.push(`Question: ${result.question}`);
  if (result.payload?.payloadPath) lines.push(`Payload: ${result.payload.payloadPath}`);
  if (result.payload?.snapshotSummary) lines.push(`Snapshot: ${JSON.stringify(result.payload.snapshotSummary)}`);
  if (result.answer?.chatAnswer || result.answer?.chat_answer) lines.push(`Answer: ${result.answer.chatAnswer || result.answer.chat_answer}`);
  else if (result.answer?.text) lines.push(`Answer: ${result.answer.text}`);
  if (result.answerPath) lines.push(`Answer file: ${result.answerPath}`);
  if (result.resultPath) lines.push(`Result: ${result.resultPath}`);
  lines.push("");
  (result.runs || []).forEach((run) => {
    lines.push(`- ${run.app?.name || "app"}: ${run.status}`);
    if (run.readyPath) lines.push(`  ready: ${run.readyPath}`);
    if (run.framesDir) lines.push(`  frames: ${run.framesDir}`);
    (run.frameRecords || []).forEach((record) => lines.push(`  frame: ${record}`));
    (run.handlerRecords || []).forEach((record) => lines.push(`  handler: ${record}`));
    if (run.error) lines.push(`  error: ${run.error}`);
  });
  return lines.join("\n");
}
function renderCityOSBuildResult(result) {
  const lines = [];
  lines.push("CityOS Docker Build");
  lines.push(`CityOS root: ${result.cityosRoot || "unknown"}`);
  lines.push(`Manifest: ${result.manifestPath || "unknown"}`);
  if (result.resultPath) lines.push(`Result: ${result.resultPath}`);
  lines.push("");
  (result.runs || []).forEach((run) => {
    lines.push(`- ${run.app?.name || "app"}: ${run.status}${run.returncode !== null && run.returncode !== undefined ? ` (${run.returncode})` : ""}`);
    lines.push(`  cwd: ${run.cwd}`);
    lines.push(`  command: ${run.commandText || (run.command || []).join(" ")}`);
    if (run.error) lines.push(`  error: ${run.error}`);
    if (run.stdout) lines.push(indentBlock("stdout", run.stdout));
    if (run.stderr) lines.push(indentBlock("stderr", run.stderr));
  });
  return lines.join("\n");
}

function indentBlock(label, text) {
  const trimmed = String(text || "").trimEnd();
  if (!trimmed) return `  ${label}:`;
  return `  ${label}:\n${trimmed.split("\n").map((line) => `    ${line}`).join("\n")}`;
}
function renderSynthArtifacts(result) {
  const apps = result?.apps || [];
  if (els.synthBuildCityOS) els.synthBuildCityOS.disabled = !apps.length;
  if (els.synthRunWebData) els.synthRunWebData.disabled = !apps.length;
  syncTellMeAnswerButton();
  els.synthAppCount.textContent = String(apps.length);
  els.synthManifestPath.textContent = result?.manifestPath || "No synthesis yet";
  els.synthAppsDirLabel.textContent = result?.appsDir || "No output yet";
  els.synthAppsStatus.textContent = apps.length
    ? `${apps.length} app${apps.length === 1 ? "" : "s"} ready to run`
    : "No apps generated";

  if (!apps.length) {
    els.synthAppsList.innerHTML = `<div class="synth-empty">Generate CityOS artifacts to see app containers, prompts, and build commands here.</div>`;
    els.synthBuildCommands.textContent = "No build commands yet.";
    updateWorkflowReadiness();
    return;
  }

  els.synthAppsList.innerHTML = apps.map((app) => {
    const agentLabel = app.agent ? `Agent ${app.agent}` : "TraceFix runtime";
    return `
      <article class="synth-app-card">
        <div class="synth-app-main">
          <span>${escapeHtml(app.kind || "app")}</span>
          <h4>${escapeHtml(app.name)}</h4>
          <p>${escapeHtml(app.path)}</p>
        </div>
        <div class="synth-app-meta">
          <small>${escapeHtml(agentLabel)}</small>
          <code>${escapeHtml(app.buildCommand || `just build app=${app.name}`)}</code>
          <code>${escapeHtml(dockerBuildCommand(app))}</code>
        </div>
      </article>
    `;
  }).join("");

  const commands = [];
  commands.push("# From your CityOS root:");
  apps.forEach((app) => commands.push(app.buildCommand || `just build app=${app.name}`));
  commands.push("");
  commands.push("# Direct Docker builds:");
  apps.forEach((app) => commands.push(dockerBuildCommand(app)));
  els.synthBuildCommands.textContent = commands.join("\n");
  updateWorkflowReadiness();
}

function dockerBuildCommand(app) {
  const appName = String(app.name || "tracefix-app").toLowerCase();
  const imageName = `cityos-${appName}:latest`;
  return `docker build -f apps/${appName}/Dockerfile -t ${imageName} .`;
}
function shellQuote(value) {
  const text = String(value || ".");
  if (/^[A-Za-z0-9_./:\\-]+$/.test(text)) return text;
  return `"${text.replaceAll('"', '\\"')}"`;
}

function renderSynthResult(result) {
  const lines = [];
  lines.push(`Workspace: ${result.workspace}`);
  lines.push(`Plan: ${result.planPath}`);
  lines.push(`Apps directory: ${result.appsDir}`);
  lines.push(`Synthesis manifest: ${result.manifestPath}`);
  lines.push("");
  lines.push("Generated Apps");
  (result.apps || []).forEach((app) => {
    lines.push(`- ${app.name} (${app.kind}${app.agent ? ` / ${app.agent}` : ""})`);
    lines.push(`  path: ${app.path}`);
    lines.push(`  build: ${app.buildCommand}`);
  });
  return lines.join("\n");
}

function normalizeModelForPayload(provider, model) {
  const trimmed = String(model || "").trim();
  if (provider === "openrouter" && trimmed.startsWith("openrouter/")) {
    return trimmed.slice("openrouter/".length);
  }
  return trimmed;
}

function updateModeFields() {
  const isRuntime = state.runMode === "runtime";
  const isPlan = state.runMode === "plan";
  const isDesign = state.runMode === "design";
  const isDesignRun = state.runMode === "design_run";
  document.querySelectorAll("[data-run-mode]").forEach((item) => {
    item.classList.toggle("active", item.dataset.runMode === state.runMode);
  });
  els.taskSourceFields.classList.toggle("hidden", isRuntime || isPlan);
  els.providerFields.classList.toggle("hidden", isPlan);
  els.keyFields.classList.toggle("hidden", isPlan);
  els.runtimeFields.classList.toggle("hidden", isDesign || isDesignRun);
  els.runtimeWorkspaceField.classList.toggle("hidden", isDesign || isDesignRun);
  els.legacyRuntimeOptions.classList.toggle("hidden", !isRuntime);
  els.tracefixRunFields.classList.toggle("hidden", !(isRuntime || isDesignRun));
  els.startRun.textContent =
    state.runMode === "design"
      ? "Generate Verified Plan"
      : state.runMode === "design_run"
        ? "Design + Run (Legacy Local)"
      : state.runMode === "plan"
        ? "Export Intermediary Plan"
        : state.runMode === "runtime"
          ? "Run Legacy Debug"
          : "Generate Verified Plan";
}

async function loadTasks() {
  const data = await getJson("/api/tasks");
  state.tasks = data.tasks || [];
  const taskOptions = state.tasks
    .map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.id)} - ${escapeHtml(task.title)}</option>`)
    .join("");
  els.taskId.innerHTML = taskOptions;
  els.synthBenchmarkSelect.innerHTML = `<option value="">All workspaces</option>${taskOptions}`;
  const defaultTask = state.tasks.find((task) => task.id === "3E") ? "3E" : state.tasks[0]?.id || "";
  els.taskId.value = defaultTask;
  els.synthBenchmarkSelect.value = defaultTask;
}

async function startRun() {
  resetRunUi();
  const provider = els.provider.value;
  const payload = {
    mode: state.runMode,
    provider,
    model: normalizeModelForPayload(provider, els.model.value),
    ...tracefixPayloadKeys(provider),
    ollamaUrl: els.ollamaUrl.value,
    taskMode: state.taskMode,
    taskId: els.taskId.value,
    // When the textarea still holds the human-readable placeholder (e.g. after a
    // page refresh or if the server's response didn't carry tellme_task_text),
    // fall back to the spec text stored in state.  The backend also resolves the
    // placeholder from the TeLLMe bridge, so either path is safe.
    customTask: (state.taskMode === "custom" &&
                 els.customTask.value.trim() === "Loaded automatically from the current TeLLMe task spec." &&
                 state.tellme.taskText)
      ? state.tellme.taskText
      : els.customTask.value,
    maxTurns: Number(els.maxTurns.value || 20),
    maxTokens: Number(els.maxTokens.value || 32768),
    temperature: Number(els.temperature.value !== "" ? els.temperature.value : 0.3),
    noSummarize: els.noSummarize.checked,
    batchLint: els.batchLint.checked,
    workspacePath: els.workspacePathInput.value,
    harness: els.harness.value,
    runtimeTask: els.runtimeTask.value,
    opencodeBin: els.opencodeBin.value,
    timeout: Number(els.timeout.value || (state.runMode === "runtime" ? 600 : 1800)),
    live: false,
    legacyDebugView: false,
  };

  try {
    const run = await postJson("/api/runs", payload);
    state.runId = run.id;
    if (run.artifacts?.workspace) {
      els.workspacePath.textContent = run.artifacts.workspace;
      state.artifacts = run.artifacts;
      renderArtifacts();
    }
    if (run.usage) {
      state.usage = run.usage;
      renderUsage();
    }
    els.startRun.disabled = true;
    els.stopRun.disabled = false;
    if (state.runMode === "runtime") {
      els.runTitle.textContent = `Legacy debug: ${payload.workspacePath || "workspace"}`;
      els.runMeta.textContent = `${payload.harness} / ${payload.model}`;
    } else if (state.runMode === "plan") {
      els.runTitle.textContent = `Exporting intermediary plan`;
      els.runMeta.textContent = payload.workspacePath || "workspace required";
    } else if (state.runMode === "design") {
      const action = "Designing";
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `${action} ${payload.taskId}` : `${action} custom task`;
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    } else if (state.runMode === "design_run") {
      const action = "Designing + running locally";
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `${action} ${payload.taskId}` : `${action} custom task`;
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    } else {
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `Planning ${payload.taskId}` : "Planning custom task";
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    }
    setStatus("running");
    connectEvents(run.id);
  } catch (error) {
    setStatus("failed");
    appendTimeline("error", error.message);
    renderOutput();
  }
}

function resetRunUi() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.runId = null;
  state.logs = [];
  state.turns = 0;
  state.tools = 0;
  state.artifacts = {};
  state.usage = null;
  state.usageDetailsOpen = false;
  els.timeline.innerHTML = "";
  els.outputView.textContent = "";
  els.workspacePath.textContent = "Waiting for intermediary plan";
  if (els.turnCount) els.turnCount.textContent = "0";
  if (els.toolCount) els.toolCount.textContent = "0";
  els.artifactCount.textContent = "0";
  els.llmUsageDetails.classList.add("hidden");
  els.llmUsageCard.setAttribute("aria-expanded", "false");
  els.graph.innerHTML = "";
  els.graphStatus.textContent = "Waiting for IR";
  renderUsage();
  updateWorkflowReadiness();
}

function connectEvents(runId, completion = null) {
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.eventSource = source;
  const settleCompletion = (ok, value) => {
    if (!completion || completion.settled) return;
    completion.settled = true;
    if (ok) completion.resolve?.(value);
    else completion.reject?.(value instanceof Error ? value : new Error(String(value || "TraceFix run failed")));
  };
  source.addEventListener("tracefix", async (message) => {
    const event = JSON.parse(message.data);
    handleRunEvent(event);
    if (event.type === "status" && ["completed", "failed", "verification_incomplete"].includes(event.status)) {
      source.close();
      state.eventSource = null;
      els.startRun.disabled = false;
      els.stopRun.disabled = true;
      const latestRun = await refreshRun();
      // If TraceFix completed with a workspace, prime the CityOS Synthesizer to
      // show it as a custom workspace so the user can synthesize without switching tabs.
      if (event.status === "completed" && state.artifacts?.workspace) {
        state.synth.workspaceType = "custom";
      }
      if (event.status === "completed") {
        settleCompletion(true, latestRun || { artifacts: state.artifacts });
      } else {
        settleCompletion(false, new Error(`TraceFix ended with status: ${event.status}`));
      }
    }
  });
  source.onerror = () => {
    source.close();
    state.eventSource = null;
    settleCompletion(false, new Error("TraceFix event stream closed before completion."));
  };
}

function handleRunEvent(event) {
  if (event.line) {
    state.logs.push(event.line);
    appendTimeline(event.type, formatTimelineLine(event.line));
    if (event.type === "turn") {
      state.turns += 1;
      if (els.turnCount) els.turnCount.textContent = String(state.turns);
    }
    if (event.type === "tool") {
      state.tools += 1;
      if (els.toolCount) els.toolCount.textContent = String(state.tools);
    }
  }

  if (event.workspace) {
    els.workspacePath.textContent = event.workspace;
  }

  if (event.type === "artifacts" && event.artifacts) {
    state.artifacts = event.artifacts;
    renderArtifacts();
  }

  if (event.type === "usage" && event.usage) {
    state.usage = event.usage;
    renderUsage();
  }

  if (event.type === "status") {
    setStatus(event.status);
  }
  if (event.type === "incomplete") {
    setStatus("verification_incomplete");
  }

  renderOutput();
}

function formatTimelineLine(line) {
  const match = String(line).match(/^\[TRACEFIX PROCEDURE SELECTED\] mode=([^\s]+)/);
  if (!match) return line;
  const label = PROCEDURE_LABELS[match[1]] || match[1].replaceAll("_", " ");
  return `Procedure: ${label}`;
}

function appendTimeline(type, line) {
  const item = document.createElement("div");
  item.className = "timeline-item";
  const kind = ["tool", "error", "turn", "pass"].includes(type) ? type : "log";
  item.innerHTML = `
    <span class="timeline-kind ${kind}">${kind}</span>
    <span class="timeline-line">${escapeHtml(line)}</span>
  `;
  els.timeline.appendChild(item);
  els.timeline.scrollTop = els.timeline.scrollHeight;
}

function setStatus(status) {
  const label = status === "verification_incomplete" ? "Verification incomplete" : status;
  const normalized =
    status === "starting" || status === "stopping"
      ? "running"
      : status === "verification_incomplete"
        ? "incomplete"
        : status;
  els.statusBadge.textContent = label;
  els.statusBadge.className = `status-badge ${normalized || "idle"}`;
  if (status === "verification_incomplete") {
    setActiveView("error");
  }
  updateWorkflowReadiness();
}

async function refreshRun() {
  if (!state.runId) return null;
  const run = await getJson(`/api/runs/${state.runId}`);
  state.artifacts = run.artifacts || {};
  state.usage = run.usage || state.usage;
  if (run.artifacts?.workspace) {
    els.workspacePath.textContent = run.artifacts.workspace;
  }
  renderArtifacts();
  renderUsage();
  renderOutput();
  return run;
}

function renderArtifacts() {
  const files = state.artifacts.files || [];
  els.artifactCount.textContent = String(files.length);
  renderGraph(state.artifacts.ir);
  updateWorkflowReadiness();
}

function renderUsage() {
  const usage = state.usage || {};
  const totalTokens = Number(usage.total_tokens || 0);
  const cost = Number(usage.estimated_cost_usd || 0);
  const estimated = usage.estimated !== false;
  const source = usage.source || "no_usage_metadata";
  const hasZeroUsage = source === "deterministic_no_llm";
  const hasUsage = totalTokens > 0 || hasZeroUsage;
  const hasCost = hasUsage && usage.cost_known !== false && source !== "no_usage_metadata";
  els.llmCost.textContent = hasCost ? `${estimated ? "~" : ""}${formatMoney(cost)}` : "Unavailable";
  els.llmTokenSummary.textContent = hasUsage
    ? `${formatCompactTokens(totalTokens)} tokens`
    : "Token usage unavailable";
  els.llmModelName.textContent = usage.model || "No model";
  els.llmUsageSource.textContent = hasUsage
    ? sourceLabel(source, estimated, usage.cost_known)
    : "Token usage not reported by provider/OpenCode for this run.";

  els.usageInputTokens.textContent = hasUsage ? formatNumber(usage.input_tokens || 0) : "Unavailable";
  els.usageOutputTokens.textContent = hasUsage ? formatNumber(usage.output_tokens || 0) : "Unavailable";
  els.usageTotalTokens.textContent = hasUsage ? formatNumber(totalTokens) : "Unavailable";
  els.usageCost.textContent = hasCost ? `${estimated ? "~" : ""}${formatMoney(cost)}` : "Unavailable";
  renderModelBreakdown(usage.model_breakdown || [], hasUsage);

  const phases = usage.phases || {};
  renderPhaseUsage("Design", phases.design, els.usageDesignTokens, els.usageDesignCost);
  renderPhaseUsage("Repair", phases.repair, els.usageRepairTokens, els.usageRepairCost);
  renderPhaseUsage("Verification", phases.verification, els.usageVerificationTokens, els.usageVerificationCost);

  const totals = usage.session_totals || {};
  els.usageTotalRuns.textContent = formatNumber(totals.total_runs || 0);
  els.usageSessionTokens.textContent = formatNumber(totals.total_tokens || 0);
  els.usageSessionCost.textContent = formatMoney(totals.total_cost_usd || 0);
}

function renderPhaseUsage(_label, phase, tokenEl, costEl) {
  const data = phase || {};
  tokenEl.textContent = formatNumber(data.total_tokens || 0);
  costEl.textContent = data.cost_known === false ? "Unavailable" : formatMoney(data.estimated_cost_usd || 0);
}

function renderModelBreakdown(entries, hasUsage) {
  const rows = Array.isArray(entries) ? entries : [];
  if (!hasUsage || !rows.length) {
    els.usageModelBreakdown.innerHTML = `<dt>No model usage</dt><dd>${hasUsage ? "0" : "Unavailable"}</dd>`;
    return;
  }
  els.usageModelBreakdown.innerHTML = rows.map((entry) => {
    const component = entry.component || "LLM";
    const model = entry.model || "unknown";
    const tokens = formatNumber(entry.total_tokens || 0);
    const cost = entry.cost_known === false ? "cost unavailable" : formatMoney(entry.estimated_cost_usd || 0);
    return `<dt>${escapeHtml(component)}<small>${escapeHtml(model)}</small></dt><dd>${tokens} tok<br><small>${escapeHtml(cost)}</small></dd>`;
  }).join("");
}

function renderOutput() {
  let content = "";
  if (state.activeView === "log") {
    content = state.logs.join("\n");
  } else if (state.activeView === "ir") {
    content = state.artifacts.ir ? JSON.stringify(state.artifacts.ir, null, 2) : "No ir.json yet.";
  } else if (state.activeView === "plan") {
    content = renderPlanSummary(state.artifacts.cityosPlan, state.artifacts.cityosPlanPath);
  } else if (state.activeView === "protocol") {
    content = state.artifacts.protocol || "No Protocol.tla yet.";
  } else if (state.activeView === "states") {
    content = state.artifacts.states ? JSON.stringify(state.artifacts.states, null, 2) : "No states.json yet.";
  } else if (state.activeView === "error") {
    content = renderTlcError();
  }
  els.outputView.textContent = content;
  if (state.activeView === "log") {
    els.outputView.scrollTop = els.outputView.scrollHeight;
  }
}

async function openArtifact(target, successText) {
  const workspacePath = currentWorkspacePath();
  if (!workspacePath) {
    showToast("No workspace selected");
    return;
  }
  try {
    await postJson("/api/open-artifact", { workspacePath, target });
    showToast(successText);
  } catch (error) {
    showToast(error.message);
  }
}

function currentWorkspacePath() {
  const workspacePath = state.artifacts.workspace || els.workspacePath.textContent.trim();
  return workspacePath.startsWith("Waiting for") ? "" : workspacePath;
}

function setActiveView(view) {
  state.activeView = view;
  document.querySelectorAll("[data-view]").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  renderOutput();
}

function renderTlcError() {
  const lines = [];
  if (state.artifacts.tlcError) {
    lines.push(state.artifacts.tlcError);
  } else {
    lines.push("No tlc_error.md found. Verification may have failed before TLC produced an error file.");
  }
  const recovery = state.artifacts.recovery;
  if (recovery) {
    lines.push("");
    lines.push("Recovery Guidance");
    lines.push(`Workspace: ${recovery.workspace}`);
    lines.push(`TLC error: ${recovery.tlcErrorPath}`);
    lines.push(`IR: ${recovery.irPath}`);
    lines.push(`Protocol: ${recovery.protocolPath}`);
    lines.push("");
    lines.push("Suggested rerun:");
    lines.push(recovery.rerunDesignCommand);
    lines.push("");
    lines.push("Manual checks:");
    (recovery.manualCommands || []).forEach((command) => lines.push(command));
    if (recovery.notes?.length) {
      lines.push("");
      lines.push("Notes:");
      recovery.notes.forEach((note) => lines.push(`- ${note}`));
    }
  }
  return lines.join("\n");
}

function renderPlanSummary(plan, planPath) {
  if (!plan) {
    return "No spec/cityos_module_plan.json yet. Use Design to generate a workspace, or choose Intermediary Plan with an existing workspace.";
  }
  const lines = [];
  lines.push(`Artifact: ${plan.artifact_type || "unknown"}`);
  lines.push(`Location: ${planPath || "spec/cityos_module_plan.json"}`);
  lines.push(`Verification: ${plan.verification?.status || plan.tracefix?.verification_status || "unknown"}`);
  lines.push(`Production ready: ${plan.verification?.production_ready === true ? "yes" : "no"}`);
  lines.push(`Export status: ${planPath ? "cityos_module_plan.json available" : "not exported"}`);
  lines.push("");
  lines.push("Application Goals");
  const goals = plan.goals || plan.application?.goals || [];
  if (!goals.length) lines.push("- No explicit goals inferred yet.");
  goals.forEach((goal) => {
    lines.push(`- ${goal.id || "goal"}: ${goal.description || ""}`);
  });
  lines.push("");
  lines.push("Generated Agents");
  const agents = plan.agents || [];
  if (!agents.length) lines.push("- No agents inferred yet.");
  agents.forEach((agent) => {
    lines.push(`- ${agent.name || agent.id}: ${agent.role || ""}`);
    if (agent.prompt_path) lines.push(`  prompt: ${agent.prompt_path}`);
    if (agent.inputs?.length) lines.push(`  inputs: ${agent.inputs.join(", ")}`);
    if (agent.outputs?.length) lines.push(`  outputs: ${agent.outputs.join(", ")}`);
  });
  lines.push("");
  lines.push("Protocol / Topology");
  const topology = plan.topology || plan.protocol?.topology || {};
  lines.push(`- agents: ${(topology.agents || []).length}`);
  lines.push(`- resources: ${(topology.resources || []).length}`);
  lines.push(`- channels: ${(topology.channels || []).length}`);
  lines.push("");
  lines.push("Communication Requirements");
  const edges = plan.communication_requirements || plan.protocol?.allowed_communication_edges || [];
  if (!edges.length) lines.push("- No explicit communication edges inferred yet.");
  edges.forEach((edge) => {
    lines.push(`- ${edge.from || "?"} -> ${edge.to || "?"} via ${edge.channel || "channel"} [${(edge.labels || []).join(", ")}]`);
  });
  lines.push("");
  lines.push("Runtime Monitor Requirements");
  lines.push(`- required: ${plan.runtime_monitor?.required === false ? "false" : "true"}`);
  const monitorRules = plan.runtime_monitor?.monitor_rules || [];
  if (!monitorRules.length) lines.push("- Monitor rules will be derived from verified protocol artifacts.");
  monitorRules.forEach((rule) => lines.push(`- ${rule}`));
  lines.push("");
  lines.push("Resource Requirements");
  const resources = plan.resource_requirements || [];
  if (!resources.length) lines.push("- No external resources declared.");
  resources.forEach((resource) => {
    lines.push(`- ${resource.id || resource.name || JSON.stringify(resource)} ${resource.type ? `(${resource.type})` : ""}`);
  });
  lines.push("");
  lines.push("Source Artifacts");
  Object.entries(plan.source_artifacts || {}).forEach(([key, value]) => {
    lines.push(`- ${key}: ${value}`);
  });
  return lines.join("\n");
}

function renderGraph(ir) {
  const svg = els.graph;
  const width = Math.max(svg.clientWidth || 600, 320);
  const height = Math.max(svg.clientHeight || 430, 320);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";

  if (!ir || !Array.isArray(ir.agents)) {
    els.graphStatus.textContent = "Waiting for IR";
    return;
  }
  els.graphStatus.textContent = "IR loaded";

  const agents = ir.agents.map((agent) => ({ id: String(agent.id || agent), type: "agent" }));
  const resources = (ir.resources || []).map((resource) => ({
    id: String(resource.id || resource),
    type: resource.type || "Lock",
  }));
  const channels = ir.channels || [];
  const center = { x: width / 2, y: height / 2 };
  const radiusX = Math.max(115, width * 0.34);
  const radiusY = Math.max(90, height * 0.3);
  const positions = new Map();

  addArrow(svg);

  agents.forEach((agent, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(agents.length, 1)) * Math.PI * 2;
    positions.set(agent.id, {
      x: center.x + Math.cos(angle) * radiusX,
      y: center.y + Math.sin(angle) * radiusY,
    });
  });
  resources.forEach((resource, index) => {
    positions.set(resource.id, {
      x: center.x + (index - (resources.length - 1) / 2) * 88,
      y: center.y,
    });
  });

  resources.forEach((resource) => {
    agents.forEach((agent) => drawLine(svg, positions.get(agent.id), positions.get(resource.id), "resource-path", false));
  });

  channels.forEach((channel, index) => {
    const froms = normalise(channel.from);
    const tos = normalise(channel.to);
    froms.forEach((from) => {
      tos.forEach((to) => {
        if (from !== to) drawCurve(svg, positions.get(from), positions.get(to), index);
      });
    });
  });

  resources.forEach((resource) => drawResource(svg, positions.get(resource.id), resource));
  agents.forEach((agent) => drawAgent(svg, positions.get(agent.id), agent));
}

function addArrow(svg) {
  const defs = node("defs", {});
  defs.innerHTML = `
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#435f93"></path>
    </marker>
  `;
  svg.appendChild(defs);
}

function normalise(value) {
  if (Array.isArray(value)) return value.map(String);
  if (value === undefined || value === null) return [];
  return [String(value)];
}

function drawLine(svg, source, target, className, arrow) {
  if (!source || !target) return;
  svg.appendChild(node("path", {
    d: `M ${source.x} ${source.y} L ${target.x} ${target.y}`,
    class: className,
    "marker-end": arrow ? "url(#arrow)" : "",
  }));
}

function drawCurve(svg, source, target, index) {
  if (!source || !target) return;
  const mx = (source.x + target.x) / 2;
  const my = (source.y + target.y) / 2;
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const len = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
  const bend = ((index % 5) - 2) * 17;
  const cx = mx + (-dy / len) * bend;
  const cy = my + (dx / len) * bend;
  svg.appendChild(node("path", {
    d: `M ${source.x} ${source.y} Q ${cx} ${cy} ${target.x} ${target.y}`,
    class: "channel-path",
    "marker-end": "url(#arrow)",
  }));
}

function drawAgent(svg, pos, agent) {
  if (!pos) return;
  svg.appendChild(node("circle", { cx: pos.x, cy: pos.y, r: 27, fill: "#dbeae4", stroke: "#2f6f5f", "stroke-width": 2 }));
  const icon = node("text", { x: pos.x, y: pos.y + 5, class: "node-label" });
  icon.textContent = initials(agent.id);
  svg.appendChild(icon);
  const label = node("text", { x: pos.x, y: pos.y + 44, class: "node-label" });
  label.textContent = compact(agent.id);
  svg.appendChild(label);
  const sub = node("text", { x: pos.x, y: pos.y + 58, class: "node-sub" });
  sub.textContent = "agent";
  svg.appendChild(sub);
}

function drawResource(svg, pos, resource) {
  if (!pos) return;
  svg.appendChild(node("rect", { x: pos.x - 26, y: pos.y - 22, width: 52, height: 44, rx: 7, fill: "#f1d8d4", stroke: "#9c3d32", "stroke-width": 2 }));
  const label = node("text", { x: pos.x, y: pos.y + 4, class: "node-label" });
  label.textContent = compact(resource.id);
  svg.appendChild(label);
  const sub = node("text", { x: pos.x, y: pos.y + 39, class: "node-sub" });
  sub.textContent = resource.type || "Lock";
  svg.appendChild(sub);
}

function node(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value !== "") el.setAttribute(key, value);
  });
  return el;
}

function initials(id) {
  return String(id)
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function compact(id) {
  const text = String(id).replaceAll("_", " ");
  return text.length > 17 ? `${text.slice(0, 16)}...` : text;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatCompactTokens(value) {
  const tokens = Number(value || 0);
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens >= 10_000_000 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens >= 100_000 ? 0 : 1)}K`;
  return String(tokens);
}

function formatMoney(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function sourceLabel(source, estimated, costKnown) {
  if (source === "deterministic_no_llm") return "No LLM call used by this run";
  if (source === "session_json") return estimated ? "Session metadata, estimated cost" : "Session metadata";
  if (source === "json_usage_event") return estimated ? "API usage metadata, estimated cost" : "API usage metadata";
  if (source?.startsWith("stdout")) return "Parsed from run output, estimated";
  if (costKnown === false) return "No pricing for this model yet";
  return estimated ? "Waiting for usage metadata" : "Usage metadata";
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
  applyTheme(getPreferredTheme());
  await loadUiInfo();
  bindEvents();
  updateKeyFields();
  updateModelSuggestions();
  syncTellMeTracefixFromPlanner();
  updateModeFields();
  updateWorkflow();
  await loadTellMeConfig();
  await loadJsonContext();
  await loadTellMeCurrent();
  await loadModelOptions();
  await loadTasks();
  renderUsage();
  renderSynthArtifacts(null);
  renderOutput();
}

init().catch((error) => {
  appendTimeline("error", error.message);
});
