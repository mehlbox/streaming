const video = document.getElementById("video");
const audio = document.getElementById("audio");
const hlsUrl = document.body?.dataset?.hlsUrl || "";
const audioHlsUrl = document.body?.dataset?.audioHlsUrl || "";
const statusEl = document.getElementById("status");
const offlineEl = document.getElementById("offline");
const playerEl = document.getElementById("player");
const unmuteEl = document.getElementById("unmute");
const unmuteBtn = unmuteEl?.querySelector("button");
const audioControlsEl = document.getElementById("audio-controls");
const audioToggleBtn = document.getElementById("audio-toggle");
const audioLabelEl = audioControlsEl?.querySelector(".audio-label");
const audioTogglePath = audioControlsEl?.querySelector(".audio-toggle-icon path");
const volumeSlider = document.getElementById("audio-volume");
const debugPanel = document.getElementById("debug-panel");
const audioOnlyToggle = document.getElementById("audio-only");
const clientsEl = document.getElementById("clients");
const nodeNameEl = document.getElementById("node-name");
const reloadBtn = document.getElementById("reload");
const scheduleCardEl = document.getElementById("schedule-card");
const scheduleEl = document.getElementById("schedule");
const scheduleEmptyEl = document.getElementById("schedule-empty");
const offlineTitleEl = document.getElementById("offline-title");
const offlineSubEl = document.getElementById("offline-sub");
const statsCanvas = document.getElementById("stats-canvas");
const statsEmptyEl = document.getElementById("stats-empty");
const statsRangeEl = document.querySelector(".stats-range");
const statsMinutesEl = document.getElementById("stats-minutes");
const statsBucketEl = document.getElementById("stats-bucket");
const statsRefreshBtn = document.getElementById("stats-refresh");
const statsSection = document.getElementById("stats-section");
const satelliteSection = document.getElementById("satellite-section");
const satelliteBody = document.getElementById("satellite-body");
const scalewaySection = document.getElementById("scaleway-section");
const scalewayForm = document.getElementById("scaleway-form");
const scalewayZoneEl = document.getElementById("scaleway-zone");
const scalewayTypeEl = document.getElementById("scaleway-type");
const scalewayCreateBtn = document.getElementById("scaleway-create");
const scalewayBody = document.getElementById("scaleway-body");
const scalewayStatusEl = document.getElementById("scaleway-status");
const scalewayMetaEl = document.getElementById("scaleway-meta");
const statusUrl = document.body?.dataset?.statusUrl || "/status";
const audioStatusUrl = document.body?.dataset?.audioStatusUrl || "/audio-status";
const adminMode = document.body?.dataset?.admin === "1";
const scheduleTheme = document.documentElement?.dataset?.theme || "ocean";
const scheduleBaseUrl = (document.body?.dataset?.scheduleBaseUrl || "/data").replace(/\/$/, "");
const scheduleUrl = document.body?.dataset?.scheduleUrl || `${scheduleBaseUrl}/schedule-${scheduleTheme}.json`;
const scheduleLocale = "de-DE";
const clientLogUrl = document.body?.dataset?.clientLogUrl || "/client-log";
const audioOnlyForced = document.body?.dataset?.audioOnly === "1";
const localNodeName = document.body?.dataset?.localNodeName || "main";
const timeFormatter = new Intl.DateTimeFormat(scheduleLocale, { hour: "2-digit", minute: "2-digit" });
const dayFormatter = new Intl.DateTimeFormat(scheduleLocale, { day: "2-digit" });
const monthFormatter = new Intl.DateTimeFormat(scheduleLocale, { month: "short" });
const weekdayFormatter = new Intl.DateTimeFormat(scheduleLocale, { weekday: "short" });
const longDateFormatter = new Intl.DateTimeFormat(scheduleLocale, {
  weekday: "long",
  day: "2-digit",
  month: "long"
});
const defaultStatsMinutes = 60;
let hls = null;
let started = false;
let isLive = false;
let autostartEnabled = true;
let audioOnlyEnabled = audioOnlyForced || (audioOnlyToggle?.checked ?? false);
let mediaEl = video;
let activeHlsUrl = hlsUrl;
let mediaRecoveryAttempts = 0;
let lastMediaRecoveryAt = 0;
let satelliteUrl = null;
let satelliteAssigned = false;
const satelliteAssignPollIntervalMs = 5000;
const satelliteExcludeCooldownMs = 30000;
const audioOnlyStorageKey = "audioOnly";
const autostartAttemptsKey = "autostartAttempts";
const autostartMaxAttempts = 10;
const volumeStorageKey = "audioVolume";
const tabIdStorageKey = "streamTabId";
const tabBroadcastKey = "streamTabBroadcast";
const tabChannelName = "stream-tabs";
const startupBufferSeconds = 3;
const startupBufferTimeoutMs = 6000;
const liveStartOffsetSeconds = 4.5;
const stallReloadGraceMs = 60000;
const stallRecoveryCooldownMs = 4000;
const satelliteStallFailoverMs = 10000;
const satelliteStartupSwitchTimeoutMs = 10000;
const playbackProgressEpsilonSeconds = 0.12;
const playbackProgressGraceMs = 15000;
const statusPollIntervalMs = 10000;
const statusFailureGraceMs = 20000;
const unmuteWatchIntervalMs = 250;
const scalewayPollIntervalMs = 10000;
let allowAutoplay = true;
const debugEnabled = document.body?.dataset?.debug === "1";
const scalewayEnabled = document.body?.dataset?.scalewayEnabled === "1";
const scalewayServerLimit = Number(document.body?.dataset?.scalewayServerLimit || "5") || 5;
const pendingScalewayDeletes = new Set();
let satelliteLastPayload = [];
let scalewayLastPayload = null;
let statsMinutes = Number.parseInt(statsMinutesEl?.value || `${defaultStatsMinutes}`, 10);
let statsBucketMinutes = Number.parseInt(statsBucketEl?.value || "5", 10);
let scheduleData = [];
let audioAvailable = false;
let audioLive = null;
let playbackActive = false;
let autostartAttempts = 0;
let tabSuppressed = false;
let tabLocked = false;
let lastLiveStatus = false;
let lastAudioLiveStatus = null;
let tabId = null;
let tabChannel = null;
let startupPlayToken = 0;
let tabLockEl = null;
let playerReplaced = false;
let stallStartedAt = 0;
let lastStallRecoveryAt = 0;
let playerStartedAt = 0;
let lastPlaybackProgressAt = 0;
let lastPlaybackPosition = null;
let currentExcludeSatelliteUrl = null;
let currentExcludeSatelliteUntil = 0;
let satelliteAssignmentPromise = null;
let satelliteSwitchPromise = null;
let satelliteStartupSwitchTimer = null;
let satelliteStartupSwitchTargetUrl = null;
let satelliteAssignmentRequestToken = 0;
let pendingPlaybackRequest = {
  shouldPlay: true,
  immediate: false,
  restoreAudio: false
};
const debugFlowMaxLines = 80;
const debugImportantMaxLines = 40;
const debugLogDefaultThrottleMs = 1500;
const debugLogPolicies = [
  { prefix: "source selected:", throttleMs: 30000 },
  { prefix: "satellite assignment:", throttleMs: 10000 },
  { prefix: "player ready", throttleMs: 10000 },
  { prefix: "autostart ", throttleMs: 5000 }
];
const debugImportantPrefixes = [
  "no satellite yet",
  "no satellite available",
  "satellite assign failed",
  "satellite fallback:",
  "satellite retry",
  "recovering playback",
  "play() failed",
  "audio stream not configured",
  "audio stream offline",
  "media error:",
  "media stalled",
  "media waiting",
  "hls load failed:",
  "stream load failed on",
  "status poll failed:",
  "error:",
  "promise:",
  "tab broadcast failed:",
  "tab message parse failed:",
  "tab locked, skip start",
  "tab suppressed, skip start",
  "no media source available",
  "satellite fetch failed:",
  "autostart reset failed:"
];
const debugLogHistory = new Map();
const debugFlowEntries = [];
const debugImportantEntries = [];

const debugLogThrottleMsForMessage = (message) => {
  const policy = debugLogPolicies.find((entry) => message.startsWith(entry.prefix));
  return policy?.throttleMs ?? debugLogDefaultThrottleMs;
};

const debugLogLevelForMessage = (message) => (
  debugImportantPrefixes.some((prefix) => message.startsWith(prefix)) ? "important" : "flow"
);

const trimDebugEntries = (entries, maxLines) => {
  if (entries.length > maxLines) {
    entries.splice(0, entries.length - maxLines);
  }
};

const renderDebugPanel = () => {
  const sections = [];
  if (debugFlowEntries.length) {
    sections.push("Ablauf", ...debugFlowEntries);
  }
  if (debugImportantEntries.length) {
    if (sections.length) sections.push("");
    sections.push("Wichtig", ...debugImportantEntries);
  }
  debugPanel.textContent = sections.length ? `${sections.join("\n")}\n` : "";
  debugPanel.scrollTop = debugPanel.scrollHeight;
};

const debugLog = (message) => {
  if (!debugPanel || !debugEnabled) return;
  const now = Date.now();
  const throttleMs = debugLogThrottleMsForMessage(message);
  const lastLoggedAt = debugLogHistory.get(message) || 0;
  if (now - lastLoggedAt < throttleMs) return;
  debugLogHistory.set(message, now);
  debugPanel.classList.remove("hidden");
  const ts = new Date().toLocaleTimeString();
  const nextLine = `[${ts}] ${message}`;
  if (debugLogLevelForMessage(message) === "important") {
    debugImportantEntries.push(nextLine);
    trimDebugEntries(debugImportantEntries, debugImportantMaxLines);
  } else {
    debugFlowEntries.push(nextLine);
    trimDebugEntries(debugFlowEntries, debugFlowMaxLines);
  }
  renderDebugPanel();
};

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

const viewerCookieValue = () => {
  const prefix = "stream_viewer=";
  const entry = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return entry ? entry.slice(prefix.length) : "";
};

const debugSatelliteLabel = (value) => {
  if (!value) return "main";
  try {
    const host = new URL(value).hostname || "";
    return host.split(".")[0] || host || value;
  } catch {
    return value;
  }
};

const activeNodeName = () => (satelliteAssigned ? debugSatelliteLabel(satelliteUrl) : localNodeName);

const updateNodeName = () => {
  if (!nodeNameEl) return;
  nodeNameEl.textContent = `${activeNodeName()}`;
};

const logNodeSwitchConsole = (event, details = {}) => {
  const payload = {
    ts: new Date().toISOString(),
    event,
    activeNode: activeNodeName(),
    satelliteAssigned,
    satelliteUrl: satelliteUrl || null,
    activeHlsUrl: activeHlsUrl || null,
    started,
    isLive,
    playbackActive,
    muted: mediaEl?.muted ?? null,
    paused: mediaEl?.paused ?? null,
    currentSrc: mediaEl?.currentSrc || mediaEl?.src || null,
    ...details
  };
  const logger = event.includes("error") || event.includes("failed") || event.includes("fallback")
    ? console.warn
    : console.info;
  logger("[node-switch]", payload);
};

const resolveHlsUrl = (baseUrl) => {
  if (!satelliteUrl) return baseUrl;
  const filename = baseUrl.split("/").pop();
  return `${satelliteUrl.replace(/\/$/, "")}/${filename}`;
};

const resetSatelliteStartupRetries = () => {};

const clearSatelliteStartupSwitchTimer = () => {
  if (satelliteStartupSwitchTimer) {
    clearTimeout(satelliteStartupSwitchTimer);
    satelliteStartupSwitchTimer = null;
  }
  satelliteStartupSwitchTargetUrl = null;
};

const scheduleSatelliteStartupSwitchCheck = (reason) => {
  if (!satelliteAssigned || !satelliteUrl) return;
  clearSatelliteStartupSwitchTimer();
  const targetUrl = satelliteUrl;
  satelliteStartupSwitchTargetUrl = targetUrl;
  satelliteStartupSwitchTimer = setTimeout(() => {
    satelliteStartupSwitchTimer = null;
    if (!satelliteAssigned || satelliteUrl !== targetUrl) return;
    const stillNotPlaying = !playbackActive || !mediaEl || mediaEl.readyState < 2;
    if (!stillNotPlaying) return;
    logNodeSwitchConsole("startup-switch-timeout", {
      reason,
      targetUrl,
      readyState: mediaEl?.readyState ?? null
    });
    void switchToAlternateSatellite(`startup timeout ${satelliteStartupSwitchTimeoutMs}ms (${reason})`);
  }, satelliteStartupSwitchTimeoutMs);
};

const performSourceSwitch = (reason, updateSource) => {
  const previousMediaEl = mediaEl;
  const previousActiveHlsUrl = activeHlsUrl;
  const shouldPlay = !!started;
  const restoreAudio = !!(started && previousMediaEl && !previousMediaEl.muted && currentVolume > 0);
  const previousNode = activeNodeName();

  updateSource();

  if (!started) return;
  if (mediaEl !== previousMediaEl || !activeHlsUrl || activeHlsUrl === previousActiveHlsUrl) {
    return;
  }

  debugLog(`source switch: ${reason}`);
  logNodeSwitchConsole("perform-source-switch", {
    reason,
    previousNode,
    nextNode: activeNodeName(),
    previousActiveHlsUrl,
    nextActiveHlsUrl: activeHlsUrl,
    shouldPlay,
    restoreAudio
  });
  setPendingPlaybackRequest({
    shouldPlay,
    immediate: shouldPlay,
    restoreAudio
  });
  clearStallState();
  resetPlaybackProgressTracking();
  playerStartedAt = Date.now();
  playbackActive = false;
  if (mediaEl) {
    mediaEl.crossOrigin = satelliteAssigned ? "use-credentials" : "";
    applyPendingAudioState();
  }

  if (hls) {
    restartPlayerForSourceChange(reason, {
      shouldPlay,
      immediate: shouldPlay,
      restoreAudio
    });
    return;
  }

  if (mediaEl && mediaEl.canPlayType("application/vnd.apple.mpegurl")) {
    mediaEl.addEventListener("loadedmetadata", () => {
      seekToNearLiveStart(mediaEl);
      continuePendingPlayback();
    }, { once: true });
    mediaEl.src = activeHlsUrl;
    mediaEl.load();
    updateUnmute();
    updateAudioControls();
    return;
  }

  restartPlayerForSourceChange(reason, {
    shouldPlay,
    immediate: shouldPlay,
    restoreAudio
  });
};

const requestSatelliteAssignment = async ({ forceFresh = false, excludeUrl = null } = {}) => {
  if (!forceFresh && satelliteAssignmentPromise) {
    logNodeSwitchConsole("assignment-reused-promise");
    return satelliteAssignmentPromise;
  }

  const requestToken = ++satelliteAssignmentRequestToken;
  let effectiveExcludeUrl = excludeUrl ?? currentExcludeSatelliteUrl;
  const requestPromise = (async () => {
    try {
      const previousSatelliteUrl = satelliteUrl;
      const previousAssigned = satelliteAssigned;
      if (effectiveExcludeUrl && currentExcludeSatelliteUntil && Date.now() >= currentExcludeSatelliteUntil) {
        currentExcludeSatelliteUrl = null;
        currentExcludeSatelliteUntil = 0;
        effectiveExcludeUrl = null;
      }
      debugLog("satellite assignment: checking");
      logNodeSwitchConsole("assignment-check", {
        excludeUrl: effectiveExcludeUrl,
        excludeUntil: currentExcludeSatelliteUntil || null,
        forceFresh
      });
      const assignUrl = effectiveExcludeUrl
        ? `/api/satellite/assign?exclude=${encodeURIComponent(effectiveExcludeUrl)}`
        : "/api/satellite/assign";
      const resp = await fetch(assignUrl, { cache: "no-store" });
      if (!resp.ok) {
        logNodeSwitchConsole("assignment-failed-response", {
          status: resp.status,
          assignUrl
        });
        return;
      }
      const data = await resp.json();
      if (requestToken !== satelliteAssignmentRequestToken) {
        logNodeSwitchConsole("assignment-stale-ignored", {
          requestToken,
          latestToken: satelliteAssignmentRequestToken,
          assignUrl,
          assignedUrl: data?.satellite_url || null
        });
        return undefined;
      }
      logNodeSwitchConsole("assignment-result", {
        assignUrl,
        assignedUrl: data?.satellite_url || null,
        previousAssigned,
        previousSatelliteUrl
      });
      return data?.satellite_url ? data.satellite_url.replace(/\/$/, "") : null;
    } catch (error) {
      debugLog(`satellite assign failed, streaming direct: ${error?.message || error}`);
      logNodeSwitchConsole("assignment-error", {
        message: error?.message || String(error)
      });
      return undefined;
    } finally {
      if (satelliteAssignmentPromise === requestPromise) {
        satelliteAssignmentPromise = null;
      }
    }
  })();

  satelliteAssignmentPromise = requestPromise;
  return requestPromise;
};

const applySatelliteAssignment = (assignedUrl, reason = "assignment") => {
  const previousSatelliteUrl = satelliteUrl;
  const previousAssigned = satelliteAssigned;
  const previousSourceLabel = previousAssigned ? (previousSatelliteUrl || "main") : "main";

  if (assignedUrl === undefined) return false;
  if (!assignedUrl) {
    if (!satelliteAssigned) {
      debugLog("no satellite available, streaming direct");
      logNodeSwitchConsole("assignment-no-satellite");
    }
    return false;
  }

  const normalizedUrl = assignedUrl.replace(/\/$/, "");
  if (normalizedUrl === window.location.origin) {
    resetSatelliteStartupRetries();
    debugLog("satellite assignment: main");
    performSourceSwitch(`${reason} ${previousSourceLabel} -> main`, () => {
      satelliteUrl = null;
      satelliteAssigned = false;
      setActiveMedia();
    });
    return true;
  }

  resetSatelliteStartupRetries();
  currentExcludeSatelliteUrl = null;
  currentExcludeSatelliteUntil = 0;
  debugLog(`satellite assignment: ${debugSatelliteLabel(normalizedUrl)}`);
  performSourceSwitch(`${reason} ${previousSourceLabel} -> ${normalizedUrl}`, () => {
    satelliteUrl = normalizedUrl;
    satelliteAssigned = true;
    setActiveMedia();
  });
  return true;
};

const fetchSatelliteAssignment = async (options = {}) => {
  const assignedUrl = await requestSatelliteAssignment(options);
  applySatelliteAssignment(assignedUrl, "assignment");
  return assignedUrl;
};

const switchToAlternateSatellite = async (reason) => {
  if (satelliteSwitchPromise) {
    logNodeSwitchConsole("switch-reused-promise", { reason });
    return satelliteSwitchPromise;
  }

  satelliteSwitchPromise = (async () => {
    const failedSatelliteUrl = satelliteUrl;
    if (!failedSatelliteUrl) {
      logNodeSwitchConsole("switch-no-failed-satellite", { reason });
      fallbackToMainServer();
      return;
    }
    debugLog(`stream load failed on ${debugSatelliteLabel(failedSatelliteUrl)}; switching`);
    logNodeSwitchConsole("switch-start", {
      reason,
      failedNode: debugSatelliteLabel(failedSatelliteUrl),
      failedSatelliteUrl
    });
    currentExcludeSatelliteUrl = failedSatelliteUrl;
    currentExcludeSatelliteUntil = Date.now() + satelliteExcludeCooldownMs;
    resetSatelliteStartupRetries();

    const assignedUrl = await requestSatelliteAssignment({
      forceFresh: true,
      excludeUrl: failedSatelliteUrl
    });
    if (
      assignedUrl
      && assignedUrl !== window.location.origin
      && assignedUrl !== failedSatelliteUrl
    ) {
      applySatelliteAssignment(assignedUrl, "failover");
    }
    if (satelliteUrl === failedSatelliteUrl || !satelliteAssigned) {
      debugLog(`stream load failed on ${debugSatelliteLabel(failedSatelliteUrl)}; fallback to main`);
      logNodeSwitchConsole("switch-fallback-main", {
        reason,
        failedNode: debugSatelliteLabel(failedSatelliteUrl),
        failedSatelliteUrl,
        assignedUrl: assignedUrl || null
      });
      fallbackToMainServer();
      return;
    }
    debugLog(`switch complete: ${debugSatelliteLabel(failedSatelliteUrl)} -> ${debugSatelliteLabel(satelliteUrl)}`);
    logNodeSwitchConsole("switch-complete", {
      reason,
      failedNode: debugSatelliteLabel(failedSatelliteUrl),
      nextNode: activeNodeName(),
      nextSatelliteUrl: satelliteUrl
    });
    scheduleSatelliteStartupSwitchCheck(reason);
  })().finally(() => {
    satelliteSwitchPromise = null;
  });

  return satelliteSwitchPromise;
};

const fallbackToMainServer = () => {
  if (!satelliteAssigned) return;
  resetSatelliteStartupRetries();
  debugLog(`satellite fallback: reverting to main server from ${satelliteUrl || "unknown"}`);
  logNodeSwitchConsole("fallback-main", {
    previousSatelliteUrl: satelliteUrl
  });
  performSourceSwitch("satellite -> main", () => {
    satelliteUrl = null;
    satelliteAssigned = false;
    setActiveMedia();
  });
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const clientLogHistory = new Map();

const shouldThrottleClientLog = (key, throttleMs) => {
  if (!throttleMs || throttleMs <= 0) return false;
  const now = Date.now();
  const last = clientLogHistory.get(key) || 0;
  if (now - last < throttleMs) {
    return true;
  }
  clientLogHistory.set(key, now);
  return false;
};

const activeMediaState = () => {
  const element = mediaEl;
  const currentTime = element && Number.isFinite(element.currentTime)
    ? Number(element.currentTime.toFixed(2))
    : null;
  return {
    started,
    live: isLive,
    playbackActive,
    audioOnlyEnabled,
    hasSource: !!(element?.currentSrc || element?.src),
    muted: element?.muted ?? null,
    paused: element?.paused ?? null,
    ended: element?.ended ?? null,
    readyState: element?.readyState ?? null,
    networkState: element?.networkState ?? null,
    currentTime
  };
};

const truncateDebugValue = (value, maxLength = 160) => {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
};

const hlsErrorSummary = (data) => {
  const details = data?.details || data?.type || "unknown";
  const responseCode = data?.response?.code ?? data?.networkDetails?.status;
  const responseText = truncateDebugValue(data?.response?.text || data?.networkDetails?.statusText || "");
  const requestUrl = data?.context?.url || data?.url || activeHlsUrl || "";
  const finalUrl = data?.networkDetails?.responseURL || data?.networkDetails?.url || "";
  const parts = [
    `type=${data?.type || "unknown"}`,
    `details=${details}`,
    `fatal=${data?.fatal ? "true" : "false"}`
  ];
  if (requestUrl) parts.push(`request=${requestUrl}`);
  if (finalUrl && finalUrl !== requestUrl) parts.push(`final=${finalUrl}`);
  if (responseCode !== undefined && responseCode !== null && responseCode !== "") {
    parts.push(`status=${responseCode}`);
  }
  if (responseText) parts.push(`response=${responseText}`);
  return parts.join(" ");
};

const hlsErrorRequestUrl = (data) => (
  data?.context?.url
  || data?.url
  || data?.networkDetails?.responseURL
  || data?.networkDetails?.url
  || ""
);

const isSameUrlOrigin = (left, right) => {
  if (!left || !right) return false;
  try {
    const leftUrl = new URL(left, window.location.href);
    const rightUrl = new URL(right, window.location.href);
    return leftUrl.origin === rightUrl.origin;
  } catch {
    return false;
  }
};

const isHlsSegmentUrl = (value) => {
  if (!value) return false;
  try {
    return /\.(?:ts|m4s|mp4|aac)(?:[?#]|$)/i.test(new URL(value, window.location.href).pathname);
  } catch {
    return /\.(?:ts|m4s|mp4|aac)(?:[?#]|$)/i.test(String(value));
  }
};

const isHlsRequestUrl = (value) => {
  if (!value) return false;
  try {
    const path = new URL(value, window.location.href).pathname;
    return /\.m3u8$/i.test(path) || /\.(?:ts|m4s|mp4|aac)$/i.test(path);
  } catch {
    return /\.(?:m3u8|ts|m4s|mp4|aac)(?:[?#]|$)/i.test(String(value));
  }
};

const triggerSatelliteRequestFailover = (reason, requestUrl, failedSatelliteUrl = satelliteUrl, details = {}) => {
  if (!satelliteAssigned || !failedSatelliteUrl || !requestUrl) return;
  if (failedSatelliteUrl !== satelliteUrl) return;
  if (!isSameUrlOrigin(requestUrl, failedSatelliteUrl) || !isHlsRequestUrl(requestUrl)) return;
  logNodeSwitchConsole("request-triggered-switch", {
    reason,
    requestUrl,
    failedSatelliteUrl,
    ...details
  });
  void switchToAlternateSatellite(`${reason} ${requestUrl}`);
};

const probeManifestRequest = async (url, credentialsMode) => {
  try {
    await fetch(url, {
      cache: "no-store",
      mode: "cors",
      credentials: credentialsMode
    });
  } catch (error) {
    void error;
  }
};

const debugManifestFailure = (data) => {
  const targetUrl = data?.context?.url || data?.url || activeHlsUrl || "";
  debugLog(`hls load failed: ${data?.details || data?.type || "unknown"}`);
  if (!debugEnabled || !targetUrl) return;
  void probeManifestRequest(targetUrl, "omit");
  if (satelliteAssigned) {
    void probeManifestRequest(targetUrl, "include");
  }
};

const postClientLog = (event, details = {}) => {
  // HTTP logging is intentionally disabled.
  void event;
  void details;
};

const emitClientDebug = (event, details = {}, options = {}) => {
  const {
    throttleMs = 0,
    sendHttpFallback = false
  } = options;
  const key = `${event}:${details?.code ?? details?.name ?? details?.reason ?? details?.type ?? ""}`;
  if (shouldThrottleClientLog(key, throttleMs)) {
    return;
  }
  if (sendHttpFallback) {
    postClientLog(event, details);
  }
};

const audioEq = document.querySelector(".audio-eq");
const audioEqBars = audioEq ? Array.from(audioEq.querySelectorAll("span")) : [];
const audioVizConfig = {
  fps: 20,
  fftSize: 256,
  smoothing: 0.8,
  minDecibels: -90,
  maxDecibels: -15,
  minScale: 0.18,
  maxScale: 1,
  maxBin: 64
};
let audioContext = null;
let audioAnalyser = null;
let audioAnalyserData = null;
let audioSourceNode = null;
let audioVizActive = false;
let audioVizRaf = null;
let audioVizLast = 0;
let audioVizDisabled = false;

const getAudioContext = () => {
  if (audioContext) return audioContext;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  audioContext = new Ctx();
  return audioContext;
};

const initAudioVisualizer = () => {
  if (audioVizDisabled || !audioEqBars.length || !audio) return false;
  const ctx = getAudioContext();
  if (!ctx) {
    audioVizDisabled = true;
    return false;
  }
  if (!audioSourceNode) {
    try {
      audioSourceNode = ctx.createMediaElementSource(audio);
    } catch (error) {
      audioVizDisabled = true;
      debugLog(`audio viz disabled: ${error?.message || error}`);
      return false;
    }
  }
  if (!audioAnalyser) {
    audioAnalyser = ctx.createAnalyser();
    audioAnalyser.fftSize = audioVizConfig.fftSize;
    audioAnalyser.smoothingTimeConstant = audioVizConfig.smoothing;
    audioAnalyser.minDecibels = audioVizConfig.minDecibels;
    audioAnalyser.maxDecibels = audioVizConfig.maxDecibels;
    audioAnalyserData = new Uint8Array(audioAnalyser.frequencyBinCount);
    audioSourceNode.connect(audioAnalyser);
    audioAnalyser.connect(ctx.destination);
  }
  return true;
};

const renderAudioVisualizer = () => {
  if (!audioVizActive || !audioAnalyser || !audioAnalyserData) return;
  const now = performance.now();
  if (now - audioVizLast < 1000 / audioVizConfig.fps) {
    audioVizRaf = requestAnimationFrame(renderAudioVisualizer);
    return;
  }
  audioVizLast = now;
  audioAnalyser.getByteFrequencyData(audioAnalyserData);
  const barCount = audioEqBars.length;
  const maxBin = Math.min(audioVizConfig.maxBin, audioAnalyserData.length);
  const step = Math.max(1, Math.floor(maxBin / barCount));
  for (let i = 0; i < barCount; i += 1) {
    const start = i * step;
    const end = Math.min(maxBin, start + step);
    let sum = 0;
    for (let j = start; j < end; j += 1) {
      sum += audioAnalyserData[j];
    }
    const avg = sum / Math.max(1, end - start);
    const normalized = avg / 255;
    const scale = audioVizConfig.minScale
      + normalized * (audioVizConfig.maxScale - audioVizConfig.minScale);
    audioEqBars[i].style.setProperty("--eq-scale", scale.toFixed(3));
  }
  audioVizRaf = requestAnimationFrame(renderAudioVisualizer);
};

const startAudioVisualizer = () => {
  if (audioVizActive) return;
  if (!initAudioVisualizer()) return;
  if (audioContext && audioContext.state === "suspended") {
    audioContext.resume().catch(() => {});
  }
  if (audioEq) audioEq.classList.add("live");
  audioVizActive = true;
  audioVizLast = 0;
  renderAudioVisualizer();
};

const stopAudioVisualizer = () => {
  if (!audioVizActive) return;
  audioVizActive = false;
  if (audioVizRaf) cancelAnimationFrame(audioVizRaf);
  audioVizRaf = null;
  if (audioEq) audioEq.classList.remove("live");
  audioEqBars.forEach((bar) => bar.style.removeProperty("--eq-scale"));
};

const shouldRunAudioVisualizer = () => {
  if (!audioOnlyEnabled || !audio || !audioEqBars.length) return false;
  if (document.hidden) return false;
  if (audio.paused || audio.ended) return false;
  if (audio.muted || audio.volume === 0) return false;
  return true;
};

const syncAudioVisualizer = () => {
  if (shouldRunAudioVisualizer()) {
    startAudioVisualizer();
  } else {
    stopAudioVisualizer();
  }
};

const loadVolume = () => {
  const stored = localStorage.getItem(volumeStorageKey);
  const value = Number.parseFloat(stored);
  return Number.isFinite(value) ? clamp(value, 0, 1) : 1;
};

const applyVolume = () => {
  if (!mediaEl) return;
  const volume = clamp(currentVolume, 0, 1);
  mediaEl.volume = volume;
  if (volume === 0) {
    mediaEl.muted = true;
  }
  if (volumeSlider) {
    volumeSlider.value = volume.toString();
  }
};

let currentVolume = loadVolume();

const setPendingPlaybackRequest = ({
  shouldPlay = true,
  immediate = false,
  restoreAudio = false
} = {}) => {
  pendingPlaybackRequest = {
    shouldPlay: !!shouldPlay,
    immediate: !!immediate,
    restoreAudio: !!restoreAudio
  };
};

const syncPlaybackIntentFromElement = (element) => {
  if (!element) return;
  if (!element.muted && element.volume > 0) {
    allowAutoplay = false;
  }
};

const applyPendingAudioState = () => {
  if (!mediaEl) return;
  applyVolume();
  if (pendingPlaybackRequest.restoreAudio && currentVolume > 0) {
    mediaEl.muted = false;
    allowAutoplay = false;
  }
};

const continuePendingPlayback = () => {
  applyPendingAudioState();
  if (!pendingPlaybackRequest.shouldPlay) {
    updateUnmute();
    updateAudioControls();
    return;
  }
  if (pendingPlaybackRequest.immediate) {
    attemptPlay(mediaEl);
  } else {
    waitForStartupBuffer(mediaEl, () => attemptPlay(mediaEl));
  }
  setTimeout(updateUnmute, 100);
  updateAudioControls();
};

const loadAutostartAttempts = () => {
  const stored = sessionStorage.getItem(autostartAttemptsKey);
  const value = Number.parseInt(stored, 10);
  return Number.isFinite(value) && value >= 0 ? value : 0;
};

const logAutostartStatus = (reason) => {
  if (!debugEnabled) return;
  if (reason === "reset") return;
  const state = autostartEnabled ? "on" : "off";
  const suffix = reason ? ` - ${reason}` : "";
  debugLog(`autostart ${state} (${autostartAttempts}/${autostartMaxAttempts})${suffix}`);
};

const setAutostartAttempts = (value, reason) => {
  autostartAttempts = Math.max(0, value);
  sessionStorage.setItem(autostartAttemptsKey, autostartAttempts.toString());
  logAutostartStatus(reason);
};

const recordAutostartAttempt = () => {
  if (!autostartEnabled) return;
  const next = autostartAttempts + 1;
  setAutostartAttempts(next, "attempt");
  if (next >= autostartMaxAttempts) {
    autostartEnabled = false;
    logAutostartStatus("disabled");
  }
};

autostartAttempts = loadAutostartAttempts();
if (autostartAttempts >= autostartMaxAttempts) {
  autostartEnabled = false;
}
logAutostartStatus("init");

const fetchAudioStatus = async () => {
  if (!audioStatusUrl) return false;
  try {
    const response = await fetch(audioStatusUrl, { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    const live = !!data?.live;
    audioLive = live;
    audioAvailable = live;
    return live;
  } catch (error) {
    return false;
  }
};

window.addEventListener("error", (event) => {
  const msg = event?.message || "Unknown error";
  debugLog(`error: ${msg}`);
  emitClientDebug(
    "window_error",
    {
      message: msg,
      source: event?.filename || "",
      line: event?.lineno ?? null
    },
    { throttleMs: 10000, sendHttpFallback: true }
  );
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event?.reason?.message || String(event?.reason || "Unknown rejection");
  debugLog(`promise: ${reason}`);
  emitClientDebug(
    "promise_rejection",
    { reason },
    { throttleMs: 10000, sendHttpFallback: true }
  );
});

window.addEventListener("pagehide", (event) => {
  postClientLog("pagehide", { persisted: !!event?.persisted });
});

const toDate = (entry) => {
  if (!entry?.date || !entry?.time) return null;
  const [year, month, day] = String(entry.date).split("-").map(Number);
  const [hour, minute] = String(entry.time).split(":").map(Number);
  if (!year || !month || !day || Number.isNaN(hour) || Number.isNaN(minute)) {
    return null;
  }
  const date = new Date(year, month - 1, day, hour, minute);
  if (Number.isNaN(date.getTime())) return null;
  return date;
};

const isSameDay = (a, b) => (
  a.getFullYear() === b.getFullYear()
  && a.getMonth() === b.getMonth()
  && a.getDate() === b.getDate()
);

const normalizeSchedule = (entries) => (
  (Array.isArray(entries) ? entries : [])
    .map((entry) => {
      const start = toDate(entry);
      if (!start) return null;
      const duration = Number.isFinite(entry.durationMinutes) ? entry.durationMinutes : 90;
      const end = new Date(start.getTime() + duration * 60000);
      return { ...entry, start, end, durationMinutes: duration };
    })
    .filter(Boolean)
    .sort((a, b) => a.start - b.start)
);

const getVisibleScheduleEntries = (now = new Date()) => (
  scheduleData.filter((entry) => entry.end >= now)
);

const updateScheduleCardVisibility = (visibleEntries) => {
  if (!scheduleCardEl) return;
  scheduleCardEl.classList.toggle("hidden", visibleEntries.length === 0);
};

const renderSchedule = (visibleEntries, now = new Date()) => {
  if (!scheduleEl) return;
  scheduleEl.innerHTML = "";
  if (!visibleEntries.length) {
    if (scheduleEmptyEl) scheduleEmptyEl.hidden = true;
    return;
  }
  if (scheduleEmptyEl) scheduleEmptyEl.hidden = true;
  visibleEntries.forEach((entry) => {
    const item = document.createElement("div");
    item.className = "schedule-item";
    if (isSameDay(now, entry.start)) item.classList.add("is-today");
    if (now >= entry.start && now <= entry.end) item.classList.add("is-live");

    const dateEl = document.createElement("div");
    dateEl.className = "schedule-date";
    const dayEl = document.createElement("strong");
    dayEl.textContent = dayFormatter.format(entry.start);
    const monthEl = document.createElement("span");
    monthEl.textContent = monthFormatter.format(entry.start);
    dateEl.append(dayEl, monthEl);

    const metaEl = document.createElement("div");
    metaEl.className = "schedule-meta";
    const titleEl = document.createElement("div");
    titleEl.className = "schedule-title";
    titleEl.textContent = entry.title || "Livestream";
    const timeEl = document.createElement("div");
    timeEl.className = "schedule-time";
    const weekday = weekdayFormatter.format(entry.start);
    timeEl.textContent = `${weekday} · ${timeFormatter.format(entry.start)} Uhr`;

    const tagEl = document.createElement("div");
    tagEl.className = "schedule-tag";
    if (now >= entry.start && now <= entry.end) {
      tagEl.textContent = "Jetzt geplant";
    } else if (isSameDay(now, entry.start)) {
      tagEl.textContent = "Heute";
    } else {
      tagEl.textContent = "Termin";
    }

    metaEl.append(titleEl, timeEl, tagEl);
    item.append(dateEl, metaEl);
    scheduleEl.appendChild(item);
  });
};

const updateOfflineMessage = () => {
  if (!offlineTitleEl || !offlineSubEl) return;
  if (!scheduleData.length) {
    offlineTitleEl.textContent = "Stream offline";
    return;
  }
  const now = new Date();
  const liveSlot = scheduleData.find((entry) => now >= entry.start && now <= entry.end);
  if (liveSlot) {
    offlineTitleEl.textContent = "Störung im Livestream";
    offlineSubEl.textContent = "Es liegt eine Störung vor. Wir arbeiten mit Hochdruck an einer Lösung.";
    return;
  }
  const todaySlot = scheduleData.find((entry) => isSameDay(now, entry.start) && now < entry.start);
  if (todaySlot) {
    offlineTitleEl.textContent = "Heute geht es los";
    offlineSubEl.textContent = `Der Stream startet um ${timeFormatter.format(todaySlot.start)} Uhr.`;
    return;
  }
  const nextSlot = scheduleData.find((entry) => entry.start > now);
  if (nextSlot) {
    offlineTitleEl.textContent = "Stream offline";
    offlineSubEl.textContent = `Nächster Stream am ${longDateFormatter.format(nextSlot.start)} um ${timeFormatter.format(nextSlot.start)} Uhr.`;
    return;
  }
  offlineTitleEl.textContent = "Stream offline";
};

const refreshScheduleUI = () => {
  const now = new Date();
  const visibleEntries = getVisibleScheduleEntries(now);
  updateScheduleCardVisibility(visibleEntries);
  renderSchedule(visibleEntries, now);
  updateOfflineMessage();
};

const loadSchedule = async () => {
  if (!scheduleUrl) {
    scheduleData = [];
    refreshScheduleUI();
    return;
  }
  try {
    const response = await fetch(scheduleUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`schedule fetch failed: ${response.status}`);
    }
    const data = await response.json();
    const entries = Array.isArray(data) ? data : (data?.items || data?.schedule || []);
    scheduleData = normalizeSchedule(entries);
  } catch (error) {
    console.log("Schedule fetch failed:", error);
    scheduleData = [];
  } finally {
    refreshScheduleUI();
  }
};

const updatePlayerClass = () => {
  if (!playerEl || playerReplaced) return;
  const audioReady = audioLive && audioAvailable;
  const liveState = audioOnlyEnabled ? audioReady : isLive;
  playerEl.className = "player-wrapper";
  if (!liveState) playerEl.classList.add("offline-mode");
  if (audioOnlyEnabled) playerEl.classList.add("audio-only");
  if (offlineEl) {
    offlineEl.className = liveState ? "offline-overlay hidden" : "offline-overlay";
  }
};

const setActiveMedia = () => {
  const wantsAudioOnly = audioOnlyForced || (audioOnlyToggle?.checked ?? audioOnlyEnabled);
  if (wantsAudioOnly && audioOnlyToggle && audioOnlyForced) {
    audioOnlyToggle.checked = true;
  }
  if (wantsAudioOnly && !audioHlsUrl && !audioOnlyForced) {
    audioOnlyEnabled = false;
    if (audioOnlyToggle) audioOnlyToggle.checked = false;
    sessionStorage.removeItem(audioOnlyStorageKey);
  } else {
    audioOnlyEnabled = wantsAudioOnly;
    if (audioOnlyToggle && !audioOnlyForced) {
      sessionStorage.setItem(audioOnlyStorageKey, audioOnlyEnabled ? "1" : "0");
    }
  }
  mediaEl = audioOnlyEnabled ? audio : video;
  if (audioOnlyEnabled) {
    activeHlsUrl = resolveHlsUrl(audioHlsUrl || "");
  } else {
    activeHlsUrl = resolveHlsUrl(hlsUrl);
  }
  debugLog(`source selected: ${audioOnlyEnabled ? "audio" : "video"} via ${debugSatelliteLabel(satelliteUrl)}`);
  resetPlaybackProgressTracking();
  updateNodeName();
  updatePlayerClass();
  applyVolume();
};

const restartPlayerForSourceChange = (reason, playbackRequest = null) => {
  if (!started) return;
  const shouldPlay = playbackRequest?.shouldPlay ?? !!(mediaEl && !mediaEl.paused && !mediaEl.ended);
  const immediate = playbackRequest?.immediate ?? shouldPlay;
  const restoreAudio = playbackRequest?.restoreAudio ?? !!(mediaEl && !mediaEl.muted && currentVolume > 0);
  debugLog(`source switch: ${reason}`);
  logNodeSwitchConsole("restart-player-for-source-change", {
    reason,
    shouldPlay,
    immediate,
    restoreAudio
  });
  setPendingPlaybackRequest({ shouldPlay, immediate, restoreAudio });
  stopPlayer();
  startPlayerWithOptions({ forcePlay: immediate, shouldPlay, restoreAudio });
};

const statsColors = () => {
  const styles = getComputedStyle(document.documentElement);
  return {
    bar: styles.getPropertyValue("--primary").trim() || "#2c3e50",
    grid: styles.getPropertyValue("--stroke").trim() || "rgba(0,0,0,0.12)",
    text: styles.getPropertyValue("--text-secondary").trim() || "#7f8c8d"
  };
};

const resizeStatsCanvas = () => {
  if (!statsCanvas) return;
  const rect = statsCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  statsCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
  statsCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = statsCanvas.getContext("2d");
  if (ctx) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
};

const drawStats = (points) => {
  if (!statsCanvas) return;
  const ctx = statsCanvas.getContext("2d");
  if (!ctx) return;
  const { bar, grid, text } = statsColors();
  const width = statsCanvas.getBoundingClientRect().width;
  const height = statsCanvas.getBoundingClientRect().height;
  ctx.clearRect(0, 0, width, height);
  if (!points || points.length === 0) {
    if (statsEmptyEl) statsEmptyEl.hidden = false;
    return;
  }
  if (statsEmptyEl) statsEmptyEl.hidden = true;

  const counts = points.map((p) => Math.max(0, Number(p.count) || 0));
  const maxCount = Math.max(1, ...counts);

  const padding = 24;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding, height - padding);
  ctx.lineTo(width - padding, height - padding);
  ctx.stroke();

  const yScale = chartHeight / maxCount;
  const barGap = Math.max(1, Math.min(chartWidth * 0.02, 6));
  const barWidth = Math.max(1, (chartWidth - (points.length - 1) * barGap) / Math.max(1, points.length));

  points.forEach((point, index) => {
    const count = Math.max(0, Number(point.count) || 0);
    const x = padding + index * (barWidth + barGap);
    const barHeight = Math.max(1, count * yScale);
    const y = height - padding - barHeight;
    ctx.fillStyle = bar;
    ctx.fillRect(x, y, barWidth, barHeight);
  });

  ctx.fillStyle = text;
  ctx.font = "11px \"Space Grotesk\", sans-serif";
  const last = points[points.length - 1];
  ctx.fillText(`${last.count} Online`, padding, padding - 8);
};

const fetchStats = async () => {
  if (!statsCanvas) return;
  try {
    statsMinutes = Math.max(1, Number.parseInt(statsMinutesEl?.value || `${statsMinutes}`, 10) || defaultStatsMinutes);
    statsBucketMinutes = Math.max(1, Number.parseInt(statsBucketEl?.value || `${statsBucketMinutes}`, 10) || 1);
    const response = await fetch(`/stats?minutes=${statsMinutes}&bucket_minutes=${statsBucketMinutes}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const points = data.points || [];
    drawStats(points);
    if (statsRangeEl) {
      const minutes = data.minutes || statsMinutes;
      const bucket = data.bucket_minutes || statsBucketMinutes;
      statsRangeEl.textContent = `Letzte ${minutes} Minuten • Balken je ${bucket} Minute${bucket === 1 ? "" : "n"}`;
    }
    const statsPeakEl = document.getElementById("stats-peak");
    if (statsPeakEl) {
      const peak = points.reduce((max, point) => Math.max(max, Number(point.count) || 0), 0);
      statsPeakEl.textContent = `${peak} Online`;
    }
  } catch (error) {
    console.log("Stats fetch failed:", error);
  }
};

const initStats = () => {
  if (!statsCanvas) return;
  resizeStatsCanvas();
  fetchStats();
  window.addEventListener("resize", () => {
    resizeStatsCanvas();
    fetchStats();
  });
  if (statsMinutesEl) statsMinutesEl.addEventListener("change", fetchStats);
  if (statsBucketEl) statsBucketEl.addEventListener("change", fetchStats);
  if (statsRefreshBtn) statsRefreshBtn.addEventListener("click", fetchStats);
  setInterval(fetchStats, 60000);
};

const renderSatelliteTable = (satellites) => {
  if (!satelliteBody || !debugEnabled) return;
  satelliteLastPayload = Array.isArray(satellites) ? satellites : [];
  if (!satellites || satellites.length === 0) {
    if (satelliteSection) satelliteSection.classList.remove("hidden");
    satelliteBody.innerHTML = '<tr><td colspan="9" class="satellite-empty">Keine Server verbunden.</td></tr>';
    return;
  }
  if (satelliteSection) satelliteSection.classList.remove("hidden");
  satelliteBody.innerHTML = satellites.map((sat) => {
    const liveBandwidth = Number(sat.bandwidth_mbps || 0);
    const measuredBandwidth = Number(sat.speedtest_upload_mbps || 0);
    const scalewayBandwidth = Number(
      scalewayLastPayload?.servers?.find((server) => String(server.node_name || "") === String(sat.name || ""))?.bandwidth_mbps || 0
    );
    const maxBandwidth = measuredBandwidth > 0 ? measuredBandwidth : scalewayBandwidth;
    const maxBandwidthLabel = measuredBandwidth > 0
      ? `${maxBandwidth.toFixed(1)} Mbps`
      : `(${maxBandwidth.toFixed(1)} Mbps)`;
    const bandwidthLabel = maxBandwidth > 0
      ? `${liveBandwidth.toFixed(1)} / ${maxBandwidthLabel}`
      : `${liveBandwidth.toFixed(1)} Mbps`;
    const healthClass = sat.healthy
      ? "sat-healthy"
      : (sat.heartbeat_healthy ? "sat-degraded" : "sat-unhealthy");
    const healthLabel = sat.healthy
      ? "OK"
      : (sat.heartbeat_healthy ? "Unhealthy" : "Offline");
    const dnsClass = sat.dns_ok ? "sat-healthy" : "sat-unhealthy";
    const dnsLabel = sat.dns_label || (sat.dns_ok ? "OK" : "Fehler");
    const probeHealthClass = sat.local ? "sat-healthy" : (sat.health_ok ? "sat-healthy" : "sat-unhealthy");
    const probeHlsClass = sat.local ? "sat-healthy" : (sat.hls_ok ? "sat-healthy" : "sat-unhealthy");
    const probeHealthLabel = sat.health_label || (sat.health_ok ? "200" : "Fail");
    const probeHlsLabel = sat.hls_label || (sat.hls_ok ? "200" : "Fail");
    const hbAge = sat.last_heartbeat_age < 60
      ? `${Math.round(sat.last_heartbeat_age)}s`
      : `${Math.round(sat.last_heartbeat_age / 60)}m`;
    const dnsTitle = escapeHtml([
      sat.dns_host ? `Host: ${sat.dns_host}` : "",
      sat.dns_addresses?.length ? `DNS: ${sat.dns_addresses.join(", ")}` : "",
      sat.observed_ip ? `Observed: ${sat.observed_ip}` : "",
      sat.dns_source ? `Source: ${sat.dns_source}` : ""
    ].filter(Boolean).join(" | "));
    const healthTitle = escapeHtml([
      sat.health_url ? `URL: ${sat.health_url}` : "",
      sat.health_status ? `Status: ${sat.health_status}` : ""
    ].filter(Boolean).join(" | "));
    const hlsTitle = escapeHtml([
      sat.hls_url ? `URL: ${sat.hls_url}` : "",
      sat.hls_status ? `Status: ${sat.hls_status}` : "",
      sat.hls_preflight_status ? `Preflight: ${sat.hls_preflight_status}` : "",
      sat.hls_allow_origin ? `Allow-Origin: ${sat.hls_allow_origin}` : "",
      sat.hls_allow_credentials ? `Allow-Credentials: ${sat.hls_allow_credentials}` : ""
    ].filter(Boolean).join(" | "));
    return `<tr>
      <td>${escapeHtml(sat.name || sat.id.slice(0, 8))}</td>
      <td>${sat.viewer_count} / ${sat.capacity_max_viewers}</td>
      <td>${sat.cpu_percent.toFixed(1)}%</td>
      <td>${bandwidthLabel}</td>
      <td><span class="sat-health ${healthClass}">${healthLabel}</span></td>
      <td title="${dnsTitle}"><span class="sat-health ${dnsClass}">${escapeHtml(dnsLabel)}</span></td>
      <td title="${healthTitle}"><span class="sat-health ${probeHealthClass}">${escapeHtml(probeHealthLabel)}</span></td>
      <td title="${hlsTitle}"><span class="sat-health ${probeHlsClass}">${escapeHtml(probeHlsLabel)}</span></td>
      <td>${hbAge}</td>
    </tr>`;
  }).join("");
};

const fetchSatellites = async () => {
  if (!satelliteSection) return;
  try {
    const resp = await fetch("/api/satellites", { cache: "no-store" });
    if (!resp.ok) return;
    const data = await resp.json();
    renderSatelliteTable(data.satellites || []);
  } catch (error) {
    debugLog(`satellite fetch failed: ${error?.message || error}`);
  }
};

const initSatellites = () => {
  if (!satelliteSection || !debugEnabled) return;
  fetchSatellites();
  setInterval(fetchSatellites, 10000);
};

const setScalewayStatus = (message, isError = false) => {
  if (!scalewayStatusEl) return;
  scalewayStatusEl.textContent = message;
  scalewayStatusEl.classList.toggle("scw-status-error", !!isError);
};

const scalewayRequest = async (url, options = {}) => {
  const resp = await fetch(url, { ...options, headers: options.headers || {}, cache: "no-store" });
  if (resp.ok) {
    const text = await resp.text();
    return text ? JSON.parse(text) : {};
  }
  const errorText = (await resp.text()).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  throw new Error(errorText || `HTTP ${resp.status}`);
};

const renderScalewayTable = (payload) => {
  if (!scalewayBody) return;
  scalewayLastPayload = payload;
  const servers = payload?.servers || [];
  if (scalewayMetaEl) {
    const count = Number(payload?.count || servers.length || 0);
    const managedCount = Number(payload?.managed_count || 0);
    scalewayMetaEl.textContent = `${count} sichtbar | ${managedCount} / ${payload?.max_servers || scalewayServerLimit} verwaltet`;
  }
  if (!servers.length) {
    scalewayBody.innerHTML = '<tr><td colspan="7" class="satellite-empty">Keine Scaleway-Server im Projekt sichtbar.</td></tr>';
    return;
  }
  scalewayBody.innerHTML = servers.map((server) => {
    const state = String(server.state || "unknown");
    const stateClass = state === "running"
      ? "sat-healthy"
      : (state === "starting" || state === "stopped in place" ? "sat-degraded" : "sat-unhealthy");
    const errorTitle = escapeHtml(server.error || "");
    const deletePending = pendingScalewayDeletes.has(String(server.id || ""));
    return `<tr>
      <td>${escapeHtml(server.name || server.id)}</td>
      <td>${escapeHtml(server.node_name || "-")}</td>
      <td>${escapeHtml(server.zone || "")}</td>
      <td title="${errorTitle}"><span class="sat-health ${stateClass}">${escapeHtml(state)}</span></td>
      <td>${escapeHtml(server.public_ip || "-")}</td>
      <td>${escapeHtml(server.commercial_type || "-")}</td>
      <td>${server.managed
        ? `<button type="button" class="scw-inline-button${deletePending ? " scw-inline-button-pending" : ""}" data-server-id="${escapeHtml(server.id)}"${deletePending ? " disabled" : ""}>${deletePending ? "Pending" : "Remove"}</button>`
        : '<span class="scw-external-label">Extern</span>'}</td>
    </tr>`;
  }).join("");
};

const fetchScalewayServers = async ({ quiet = false } = {}) => {
  if (!scalewaySection || !debugEnabled) return;
  if (!scalewayEnabled) {
    setScalewayStatus("Scaleway-Verwaltung ist serverseitig nicht konfiguriert.", true);
    return;
  }
  if (!quiet) {
    setScalewayStatus("Lade Scaleway-Server ...");
  }
  try {
    const payload = await scalewayRequest("/api/scaleway/servers");
    renderScalewayTable(payload);
    if (satelliteLastPayload.length) {
      renderSatelliteTable(satelliteLastPayload);
    }
    if (!quiet) {
      setScalewayStatus("Scaleway-Server geladen.");
    }
  } catch (error) {
    renderScalewayTable({ servers: [], count: 0, managed_count: 0, max_servers: scalewayServerLimit });
    setScalewayStatus(`Scaleway-Liste fehlgeschlagen: ${error.message}`, true);
  }
};

const createScalewayServer = async () => {
  if (!scalewayForm) return;
  const payload = {
    zone: scalewayZoneEl?.value?.trim() || "",
    commercial_type: scalewayTypeEl?.value?.trim() || ""
  };
  setScalewayStatus("Erstelle Scaleway-Server ...");
  if (scalewayCreateBtn) scalewayCreateBtn.disabled = true;
  try {
    await scalewayRequest("/api/scaleway/servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    setScalewayStatus("Scaleway-Server erstellt. Cloud-init wurde hinterlegt und verifiziert.");
    await fetchScalewayServers();
  } catch (error) {
    setScalewayStatus(`Scaleway-Erstellung fehlgeschlagen: ${error.message}`, true);
  } finally {
    if (scalewayCreateBtn) scalewayCreateBtn.disabled = false;
  }
};

const deleteScalewayServer = async (serverId) => {
  if (!serverId) return;
  if (pendingScalewayDeletes.has(serverId)) return;
  pendingScalewayDeletes.add(serverId);
  if (scalewayLastPayload) {
    renderScalewayTable(scalewayLastPayload);
  }
  setScalewayStatus(`Lösche ${serverId} ...`);
  try {
    await scalewayRequest(`/api/scaleway/servers/${encodeURIComponent(serverId)}`, {
      method: "DELETE"
    });
    setScalewayStatus(`Scaleway-Server ${serverId} gelöscht.`);
    pendingScalewayDeletes.delete(serverId);
    await fetchScalewayServers();
  } catch (error) {
    setScalewayStatus(`Scaleway-Löschen fehlgeschlagen: ${error.message}`, true);
    pendingScalewayDeletes.delete(serverId);
    await fetchScalewayServers();
  }
};

const initScaleway = () => {
  if (!scalewaySection || !debugEnabled) return;
  if (scalewayForm) {
    scalewayForm.addEventListener("submit", (event) => {
      event.preventDefault();
      createScalewayServer();
    });
  }
  if (scalewayBody) {
    scalewayBody.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-server-id]");
      if (!button) return;
      deleteScalewayServer(button.getAttribute("data-server-id"));
    });
  }
  fetchScalewayServers();
  setInterval(() => {
    fetchScalewayServers({ quiet: true });
  }, scalewayPollIntervalMs);
};

const markStall = () => {
  if (!stallStartedAt) {
    stallStartedAt = Date.now();
  }
};

const clearStallState = () => {
  stallStartedAt = 0;
  lastStallRecoveryAt = 0;
  mediaRecoveryAttempts = 0;
  clearSatelliteStartupSwitchTimer();
};

const resetPlaybackProgressTracking = () => {
  lastPlaybackProgressAt = 0;
  lastPlaybackPosition = null;
};

const capturePlaybackProgress = (element, now = Date.now()) => {
  if (!element) return;
  const currentTime = element.currentTime;
  if (!Number.isFinite(currentTime)) return;
  if (!Number.isFinite(lastPlaybackPosition)) {
    lastPlaybackPosition = currentTime;
    lastPlaybackProgressAt = now;
    return;
  }
  if (Math.abs(currentTime - lastPlaybackPosition) >= playbackProgressEpsilonSeconds) {
    lastPlaybackPosition = currentTime;
    lastPlaybackProgressAt = now;
  }
};

const isPlaybackRunning = (element, now = Date.now()) => {
  if (!element || element.paused || element.ended) return false;
  if (element.readyState < 2) return false;
  capturePlaybackProgress(element, now);
  if (!lastPlaybackProgressAt) {
    lastPlaybackProgressAt = now;
    return true;
  }
  return (now - lastPlaybackProgressAt) <= playbackProgressGraceMs;
};

const tryInlineStallRecovery = (reason) => {
  if (!isLive || !mediaEl || mediaEl.ended) return;
  const now = Date.now();
  if (now - lastStallRecoveryAt < stallRecoveryCooldownMs) return;
  if (playerStartedAt && now - playerStartedAt < startupBufferTimeoutMs) return;
  lastStallRecoveryAt = now;
  debugLog(`recovering playback (${reason})`);
  emitClientDebug(
    "media_recover_attempt",
    { reason, withHls: !!hls, attempts: mediaRecoveryAttempts },
    { throttleMs: 5000 }
  );
  try {
    if (hls) {
      if (mediaRecoveryAttempts <= 2) {
        hls.recoverMediaError();
      } else if (typeof hls.startLoad === "function") {
        hls.startLoad(-1);
      }
      jumpToLiveEdge(hls, mediaEl);
    } else {
      seekToNearLiveStart(mediaEl);
    }
    attemptPlay(mediaEl);
  } catch (error) {
    console.log("Inline media recovery failed:", error);
  }
};

const attemptMediaRecovery = () => {
  if (!isLive) return;
  markStall();
  const now = Date.now();
  if (satelliteAssigned && stallStartedAt && now - stallStartedAt >= satelliteStallFailoverMs) {
    logNodeSwitchConsole("stall-triggered-switch", {
      stallAgeMs: now - stallStartedAt
    });
    void switchToAlternateSatellite(`stall ${now - stallStartedAt}ms`);
    return;
  }
  if (now - lastMediaRecoveryAt > 30000) {
    mediaRecoveryAttempts = 0;
  }
  lastMediaRecoveryAt = now;
  mediaRecoveryAttempts += 1;
  tryInlineStallRecovery("hls-error");
  if (stallStartedAt && now - stallStartedAt >= stallReloadGraceMs) {
    if (autostartEnabled) {
      forceReload();
    }
    clearStallState();
  }
};

const forceReload = () => {
  const url = new URL(window.location.href);
  url.searchParams.set("reload", Date.now().toString());
  if (satelliteUrl) {
    url.searchParams.set("sat_exclude", satelliteUrl);
  } else {
    url.searchParams.delete("sat_exclude");
  }
  window.location.replace(url.toString());
};
const url = new URL(window.location.href);
const claimOnLoad = url.searchParams.get("claim") === "1";
const excludeSatelliteUrl = url.searchParams.get("sat_exclude") || null;
currentExcludeSatelliteUrl = excludeSatelliteUrl;
if (url.searchParams.has("reload") || claimOnLoad || url.searchParams.has("sat_exclude")) {
  url.searchParams.delete("reload");
  url.searchParams.delete("claim");
  url.searchParams.delete("sat_exclude");
  history.replaceState(null, "", url.toString());
}
if (audioOnlyToggle) {
  if (audioOnlyForced) {
    audioOnlyToggle.checked = true;
    audioOnlyToggle.disabled = true;
    audioOnlyEnabled = true;
  } else {
    const storedAudioOnly = sessionStorage.getItem(audioOnlyStorageKey);
    if (storedAudioOnly === "1") {
      audioOnlyToggle.checked = true;
    } else if (storedAudioOnly === "0") {
      audioOnlyToggle.checked = false;
    }
    audioOnlyEnabled = audioOnlyToggle.checked;
  }
}
setActiveMedia();
fetchSatelliteAssignment();
setInterval(() => {
  if (satelliteAssigned) return;
  void fetchSatelliteAssignment();
}, satelliteAssignPollIntervalMs);
setInterval(() => {
  if (tabSuppressed || !isLive) return;
  const element = mediaEl;
  if (!element || !started || element.ended) return;
  const isPlaying = isPlaybackRunning(element);
  if (isPlaying) {
    clearStallState();
    return;
  }
  if (element.paused) return;
  markStall();
  const stallAge = Date.now() - stallStartedAt;
  if (satelliteAssigned && stallAge >= satelliteStallFailoverMs) {
    logNodeSwitchConsole("watchdog-triggered-switch", {
      stallAgeMs: stallAge
    });
    void switchToAlternateSatellite(`watchdog stall ${stallAge}ms`);
    return;
  }
  if (stallAge < stallReloadGraceMs) {
    tryInlineStallRecovery("watchdog");
    return;
  }
  forceReload();
  clearStallState();
}, 10000);
if (!adminMode) {
  loadSchedule();
  setInterval(refreshScheduleUI, 60000);
}
if (debugEnabled) {
  if (statsSection) statsSection.classList.remove("hidden");
  initStats();
} else if (statsSection) {
  statsSection.classList.add("hidden");
}
initSatellites();
initScaleway();
document.addEventListener("visibilitychange", () => {
  syncAudioVisualizer();
  updateUnmute();
});
setInterval(watchUnmuteState, unmuteWatchIntervalMs);


if (audioOnlyToggle) {
  audioOnlyToggle.addEventListener("change", () => {
    setActiveMedia();
    if (started) {
      stopPlayer();
    }
    if (audioOnlyEnabled) {
      updateUnmute();
      updateAudioControls();
      return;
    }
    allowAutoplay = true;
    if (isLive) {
      if (allowAutoplay && mediaEl) {
        mediaEl.muted = true;
      } else if (mediaEl) {
        mediaEl.muted = false;
      }
      updateUnmute();
      startPlayer();
      if (!allowAutoplay && mediaEl) {
        mediaEl.play().catch(() => {});
      }
    } else {
      updateUnmute();
    }
  });
}

if (reloadBtn) {
  reloadBtn.addEventListener("click", () => {
    forceReload();
  });
}

const seekToNearLiveStart = (element, livePosition = null) => {
  if (!element) return;
  const basePosition = Number.isFinite(livePosition)
    ? livePosition
    : (Number.isFinite(element.duration) ? element.duration : null);
  if (!Number.isFinite(basePosition)) return;
  const target = Math.max(0, basePosition - liveStartOffsetSeconds);
  if (!Number.isFinite(element.currentTime) || Math.abs(element.currentTime - target) > 1) {
    element.currentTime = target;
  }
};

const jumpToLiveEdge = (hlsInstance, element) => {
  const livePos = hlsInstance?.liveSyncPosition;
  seekToNearLiveStart(element, livePos);
};

const updateUnmute = () => {
  if (!unmuteEl) return;
  if (!mediaEl) return;
  if (audioOnlyEnabled) {
    if (unmuteEl.className !== "unmute-overlay hidden") {
      unmuteEl.className = "unmute-overlay hidden";
    }
    updateAudioControls();
    return;
  }
  const hasSource = !!(mediaEl.currentSrc || mediaEl.src);
  const show = isLive && started && hasSource && mediaEl.muted;
  const nextClass = show ? "unmute-overlay" : "unmute-overlay hidden";
  if (unmuteEl.className !== nextClass) {
    unmuteEl.className = nextClass;
  }
  updateAudioControls();
};

function watchUnmuteState() {
  updateUnmute();
}

const attemptPlay = (element) => {
  if (!element) return;
  element.play().catch((error) => {
    debugLog(`play() failed: ${error?.message || error}`);
    emitClientDebug(
      "play_failed",
      { name: error?.name || "unknown", message: error?.message || String(error) },
      { throttleMs: 10000 }
    );
    updateAudioControls();
    const message = (error?.message || "").toLowerCase();
    if (audioOnlyEnabled && (error?.name === "NotSupportedError" || message.includes("no supported source"))) {
      stopPlayer();
      updateAudioControls();
    }
  });
};

const updateAudioControls = () => {
  if (!audioControlsEl) return;
  if (!audioOnlyEnabled || !mediaEl) {
    audioControlsEl.className = "audio-controls hidden";
    stopAudioVisualizer();
    return;
  }
  const audioReady = audioLive && audioAvailable;
  if (!audioReady) {
    audioControlsEl.className = "audio-controls hidden";
    stopAudioVisualizer();
    return;
  }
  const isPlaying = !mediaEl.paused && !mediaEl.ended;
  const isAudible = isPlaying && !mediaEl.muted;
  audioControlsEl.className = isAudible ? "audio-controls playing" : "audio-controls idle";
  if (audioLabelEl) {
    audioLabelEl.textContent = isAudible ? "Audio pausieren" : "Audio starten";
  }
  if (audioToggleBtn) {
    audioToggleBtn.setAttribute("aria-label", isAudible ? "Audio pausieren" : "Audio starten");
  }
  if (volumeSlider) {
    volumeSlider.value = clamp(currentVolume, 0, 1).toString();
  }
  if (audioTogglePath) {
    audioTogglePath.setAttribute(
      "d",
      isAudible ? "M6 5h4v14H6zm8 0h4v14h-4z" : "M8 5v14l11-7z"
    );
  }
  syncAudioVisualizer();
};

const setStatus = (live) => {
  if (!statusEl) return;
  isLive = live;
  if (!live) {
    clearStallState();
  }
  statusEl.textContent = live ? "● Online" : "● Offline";
  statusEl.className = live ? "status-badge status-online" : "status-badge status-offline";
  const statusKpiEl = document.getElementById("status-kpi");
  if (statusKpiEl) statusKpiEl.textContent = live ? "Online" : "Offline";
  updatePlayerClass();
  updateOfflineMessage();
  updateUnmute();
  updateAudioControls();
};

const updateViewerCount = (count) => {
  if (!clientsEl) return;
  const normalized = Number.isFinite(count) ? count : 0;
  clientsEl.textContent = adminMode ? `${normalized} Online` : `👥 ${normalized} Online`;
};

if (unmuteBtn) {
  unmuteBtn.addEventListener("click", () => {
    if (!mediaEl) return;
    mediaEl.muted = false;
    attemptPlay(mediaEl);
    allowAutoplay = false;
    syncPlaybackIntentFromElement(mediaEl);
    updateUnmute();
  });
}

if (audioToggleBtn) {
  audioToggleBtn.addEventListener("click", async () => {
    if (!audioOnlyEnabled || !mediaEl) return;
    if (!audioHlsUrl) {
      audioAvailable = false;
      debugLog("audio stream not configured");
      updateAudioControls();
      return;
    }
    let ready = audioLive;
    if (ready === null) {
      ready = await fetchAudioStatus();
    }
    audioAvailable = ready;
    if (!ready) {
      debugLog("audio stream offline");
      updateAudioControls();
      return;
    }
    const hasSource = !!(mediaEl.currentSrc || mediaEl.src);
    if (!started || !hasSource) {
      setActiveMedia();
      allowAutoplay = false;
      mediaEl.muted = false;
      syncPlaybackIntentFromElement(mediaEl);
      startPlayerWithOptions({ forcePlay: true });
      updateAudioControls();
      return;
    }
    if (mediaEl.muted || mediaEl.paused || mediaEl.ended) {
      mediaEl.muted = false;
      syncPlaybackIntentFromElement(mediaEl);
      attemptPlay(mediaEl);
    } else {
      stopPlayer();
    }
    updateAudioControls();
  });
}

if (volumeSlider) {
  volumeSlider.value = clamp(currentVolume, 0, 1).toString();
  volumeSlider.addEventListener("input", () => {
    const next = Number.parseFloat(volumeSlider.value);
    if (!Number.isFinite(next)) return;
    currentVolume = clamp(next, 0, 1);
    localStorage.setItem(volumeStorageKey, currentVolume.toString());
    if (mediaEl) {
      mediaEl.volume = currentVolume;
      mediaEl.muted = currentVolume === 0;
      syncPlaybackIntentFromElement(mediaEl);
    }
    updateAudioControls();
    updateUnmute();
  });
}

[video, audio].forEach((element) => {
  if (!element) return;
  element.addEventListener("timeupdate", (event) => {
    if (event.currentTarget !== mediaEl) return;
    capturePlaybackProgress(event.currentTarget);
  });
  element.addEventListener("volumechange", (event) => {
    if (event.currentTarget === mediaEl) {
      syncPlaybackIntentFromElement(event.currentTarget);
    }
    updateUnmute();
  });
  element.addEventListener("play", updateUnmute);
  element.addEventListener("playing", (event) => {
    if (event.currentTarget !== mediaEl) return;
    clearStallState();
    playbackActive = true;
    capturePlaybackProgress(event.currentTarget);
    if (autostartAttempts > 0) {
      setAutostartAttempts(0, "reset");
    }
    updateUnmute();
  });
  element.addEventListener("pause", (event) => {
    if (event.currentTarget === mediaEl) {
      playbackActive = false;
      updateUnmute();
    }
    updateAudioControls();
  });
  element.addEventListener("ended", (event) => {
    if (event.currentTarget !== mediaEl) return;
    playbackActive = false;
    updateUnmute();
  });
  element.addEventListener("error", (event) => {
    const err = event?.currentTarget?.error;
    const code = err?.code ?? "unknown";
    debugLog(`media error: code=${code}`);
    emitClientDebug(
      "media_error",
      {
        code,
        message: err?.message || "",
        mediaType: event?.currentTarget?.tagName?.toLowerCase() || "unknown"
      },
      { throttleMs: 5000 }
    );
    if (code === 4 && audioOnlyEnabled && started) {
      audioAvailable = false;
      stopPlayer();
      updateAudioControls();
    }
    if (event.currentTarget === mediaEl) {
      playbackActive = false;
      if (satelliteAssigned) {
        logNodeSwitchConsole("media-error-triggered-switch", {
          code,
          message: err?.message || ""
        });
        void switchToAlternateSatellite(`media error code=${code}`);
        updateUnmute();
        return;
      }
      updateUnmute();
    }
  });
  element.addEventListener("stalled", (event) => {
    debugLog("media stalled");
    emitClientDebug("media_stalled", {}, { throttleMs: 15000 });
    if (event.currentTarget === mediaEl) {
      markStall();
      tryInlineStallRecovery("stalled");
      playbackActive = false;
      updateUnmute();
    }
  });
  element.addEventListener("waiting", (event) => {
    debugLog("media waiting");
    emitClientDebug("media_waiting", {}, { throttleMs: 15000 });
    if (event.currentTarget === mediaEl) {
      markStall();
      tryInlineStallRecovery("waiting");
      playbackActive = false;
      updateUnmute();
    }
  });
  element.addEventListener("canplay", (event) => {
    if (event.currentTarget === mediaEl) {
      clearSatelliteStartupSwitchTimer();
    }
    debugLog("player ready");
  });
});

const resetMediaElement = (element) => {
  if (!element) return;
  element.pause();
  element.removeAttribute("src");
  element.load();
};

const stopPlayer = () => {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  clearStallState();
  resetPlaybackProgressTracking();
  playerStartedAt = 0;
  started = false;
  playbackActive = false;
  startupPlayToken += 1;
  stopAudioVisualizer();
  resetMediaElement(video);
  resetMediaElement(audio);
};

const replacePlayerWithLockMessage = () => {
  if (!playerEl || playerReplaced) return;
  tabLockEl = document.createElement("div");
  tabLockEl.className = "player-locked";
  tabLockEl.innerHTML = `
    <div class="player-locked-card">
      <h3>Stream läuft in einem anderen Tab</h3>
      <p>Bitte nutze den neuen Tab oder lade erneut um hier weiter zu machen.</p>
      <button type="button" class="ghost small">Neu laden</button>
    </div>
  `;
  const btn = tabLockEl.querySelector("button");
  if (btn) {
    btn.addEventListener("click", () => {
      try {
        sessionStorage.removeItem(autostartAttemptsKey);
      } catch (error) {
        debugLog(`autostart reset failed: ${error?.message || error}`);
      }
      const reloadUrl = new URL(window.location.href);
      reloadUrl.searchParams.set("claim", "1");
      reloadUrl.searchParams.set("reload", Date.now().toString());
      window.location.replace(reloadUrl.toString());
    });
  }
  playerEl.replaceWith(tabLockEl);
  playerReplaced = true;
};

const bufferedAheadSeconds = (element) => {
  if (!element) return 0;
  try {
    const { buffered, currentTime } = element;
    if (!buffered || buffered.length === 0) return 0;
    for (let i = 0; i < buffered.length; i += 1) {
      const start = buffered.start(i);
      const end = buffered.end(i);
      if (currentTime >= start && currentTime <= end) {
        return Math.max(0, end - currentTime);
      }
    }
  } catch (error) {
    return 0;
  }
  return 0;
};

const waitForStartupBuffer = (element, onReady) => {
  if (!element) return;
  const token = ++startupPlayToken;
  const startedAt = performance.now();
  const check = () => {
    if (token !== startupPlayToken) return;
    if (!element || tabSuppressed) return;
    const readyStateOk = element.readyState >= 3;
    const bufferedAhead = bufferedAheadSeconds(element);
    if (readyStateOk && bufferedAhead >= startupBufferSeconds) {
      onReady();
      return;
    }
    if (performance.now() - startedAt >= startupBufferTimeoutMs) {
      onReady();
      return;
    }
    requestAnimationFrame(check);
  };
  requestAnimationFrame(check);
};

const getTabId = () => {
  let id = sessionStorage.getItem(tabIdStorageKey);
  if (!id) {
    id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(tabIdStorageKey, id);
  }
  return id;
};

const setTabSuppressed = (suppressed, reason) => {
  if (tabSuppressed === suppressed) return;
  tabSuppressed = suppressed;
  if (tabSuppressed && started) {
    stopPlayer();
  }
  if (tabSuppressed) {
    tabLocked = true;
    autostartEnabled = false;
    replacePlayerWithLockMessage();
  }
  if (!tabSuppressed) {
    handleStatus(lastLiveStatus, lastAudioLiveStatus);
  }
  updateUnmute();
  updateAudioControls();
  debugLog(`tab ${tabSuppressed ? "suppressed" : "active"}${reason ? ` (${reason})` : ""}`);
};

const broadcastTabState = (payload) => {
  if (tabChannel) {
    tabChannel.postMessage(payload);
    return;
  }
  try {
    localStorage.setItem(tabBroadcastKey, JSON.stringify({ ...payload, ts: Date.now() }));
    localStorage.removeItem(tabBroadcastKey);
  } catch (error) {
    debugLog(`tab broadcast failed: ${error?.message || error}`);
  }
};

const claimActiveTab = (reason) => {
  setTabSuppressed(false, reason);
  broadcastTabState({ type: "claim", tabId, reason });
};

const handleTabMessage = (message) => {
  if (!message || message.tabId === tabId) return;
  if (message.type === "claim") {
    setTabSuppressed(true, "remote");
  }
};

const initTabControl = () => {
  tabId = getTabId();
  if ("BroadcastChannel" in window) {
    tabChannel = new BroadcastChannel(tabChannelName);
    tabChannel.onmessage = (event) => handleTabMessage(event.data);
  } else {
    window.addEventListener("storage", (event) => {
      if (event.key !== tabBroadcastKey || !event.newValue) return;
      try {
        handleTabMessage(JSON.parse(event.newValue));
      } catch (error) {
        debugLog(`tab message parse failed: ${error?.message || error}`);
      }
    });
  }
};

const startPlayer = () => {
  startPlayerWithOptions({ forcePlay: false });
};

const startPlayerWithOptions = ({
  forcePlay,
  shouldPlay = true,
  restoreAudio = !!(mediaEl && !mediaEl.muted && currentVolume > 0)
}) => {
  if (started) return;
  if (tabLocked) {
    debugLog("tab locked, skip start");
    return;
  }
  if (tabSuppressed && !forcePlay) {
    debugLog("tab suppressed, skip start");
    return;
  }
  if (tabSuppressed && forcePlay) {
    claimActiveTab("user-play");
  } else {
    claimActiveTab(forcePlay ? "play" : "autostart");
  }
  setActiveMedia();
  if (!activeHlsUrl) {
    debugLog("no media source available");
    logNodeSwitchConsole("start-player-no-source", {
      forcePlay,
      shouldPlay,
      restoreAudio
    });
    updateAudioControls();
    return;
  }
  logNodeSwitchConsole("start-player", {
    forcePlay,
    shouldPlay,
    restoreAudio,
    mediaType: audioOnlyEnabled ? "audio" : "video"
  });
  started = true;
  playerStartedAt = Date.now();
  setStatus(true);
  setPendingPlaybackRequest({
    shouldPlay,
    immediate: !!forcePlay,
    restoreAudio
  });
  if (mediaEl) {
    mediaEl.crossOrigin = satelliteAssigned ? "use-credentials" : "";
    applyPendingAudioState();
  }

  if (mediaEl && mediaEl.canPlayType("application/vnd.apple.mpegurl")) {
    mediaEl.src = activeHlsUrl;
    if (shouldPlay && forcePlay) {
      attemptPlay(mediaEl);
    }
    mediaEl.addEventListener("loadedmetadata", () => {
      seekToNearLiveStart(mediaEl);
      continuePendingPlayback();
    }, { once: true });
    return;
  }

  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({
      liveSyncDurationCount: 6,
      liveMaxLatencyDurationCount: 12,
      maxLiveSyncPlaybackRate: 1.2,
      lowLatencyMode: false,
      maxBufferLength: 30,
      maxMaxBufferLength: 60,
      backBufferLength: 30,
      manifestLoadingTimeOut: satelliteAssigned ? 10000 : 15000,
      manifestLoadingMaxRetry: 0,
      levelLoadingTimeOut: satelliteAssigned ? 10000 : 15000,
      levelLoadingMaxRetry: 0,
      fragLoadingTimeOut: satelliteAssigned ? 10000 : 15000,
      fragLoadingMaxRetry: 0,
      manifestLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: satelliteAssigned ? 10000 : 15000,
          maxLoadTimeMs: satelliteAssigned ? 10000 : 15000,
          timeoutRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          },
          errorRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          }
        }
      },
      playlistLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: satelliteAssigned ? 10000 : 15000,
          maxLoadTimeMs: satelliteAssigned ? 10000 : 15000,
          timeoutRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          },
          errorRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          }
        }
      },
      fragLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: satelliteAssigned ? 10000 : 15000,
          maxLoadTimeMs: satelliteAssigned ? 10000 : 15000,
          timeoutRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          },
          errorRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          }
        }
      },
      keyLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: satelliteAssigned ? 10000 : 15000,
          maxLoadTimeMs: satelliteAssigned ? 10000 : 15000,
          timeoutRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          },
          errorRetry: {
            maxNumRetry: 0,
            retryDelayMs: 0,
            maxRetryDelayMs: 0
          }
        }
      },
      xhrSetup: (xhr, url) => {
        xhr.withCredentials = satelliteAssigned;
        const requestUrl = url || "";
        const failedSatelliteUrl = satelliteUrl;
        if (!satelliteAssigned || !failedSatelliteUrl || !isHlsRequestUrl(requestUrl)) return;
        let failoverTriggered = false;
        const failover = (reason) => {
          if (failoverTriggered) return;
          failoverTriggered = true;
          triggerSatelliteRequestFailover(reason, requestUrl, failedSatelliteUrl, {
            status: xhr.status || 0,
            readyState: xhr.readyState
          });
        };
        xhr.addEventListener("error", () => failover("xhr-error"));
        xhr.addEventListener("timeout", () => failover("xhr-timeout"));
        xhr.addEventListener("abort", () => failover("xhr-abort"));
        xhr.addEventListener("loadend", () => {
          const status = xhr.status || 0;
          if (status === 0 || status >= 400) {
            failover(`xhr-status-${status}`);
          }
        });
      },
      fetchSetup: (context, initParams) => (
        new Request(context.url, {
          ...initParams,
          credentials: satelliteAssigned ? "include" : "same-origin"
        })
      )
    });
    hls.loadSource(activeHlsUrl);
    if (mediaEl) {
      hls.attachMedia(mediaEl);
    }
    if (shouldPlay && forcePlay) {
      attemptPlay(mediaEl);
    }
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      resetSatelliteStartupRetries();
      jumpToLiveEdge(hls, mediaEl);
      continuePendingPlayback();
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      const details = data?.details || "";
      const requestUrl = hlsErrorRequestUrl(data);
      const responseCode = data?.response?.code ?? data?.networkDetails?.status ?? 0;
      const failedSatelliteSegment = (
        satelliteAssigned
        && isHlsSegmentUrl(requestUrl)
        && isSameUrlOrigin(requestUrl, satelliteUrl)
      );
      const isNetworkLoadFailure = (
        data?.type === Hls.ErrorTypes.NETWORK_ERROR
        || [
          "manifestLoadError",
          "manifestLoadTimeOut",
          "levelLoadError",
          "levelLoadTimeOut",
          "fragLoadError",
          "fragLoadTimeOut",
          "keyLoadError",
          "keyLoadTimeOut"
        ].includes(details)
      );
      debugLog(`hls load failed: ${details || data?.type || "unknown"}`);
      logNodeSwitchConsole("hls-error", {
        type: data?.type || "unknown",
        details: details || "unknown",
        fatal: !!data?.fatal,
        responseCode: responseCode || null,
        requestUrl: requestUrl || null,
        failedSatelliteSegment
      });
      debugManifestFailure(data);
      emitClientDebug(
        "hls_error",
        {
          type: data?.type || "unknown",
          details: details || "unknown",
          fatal: !!data?.fatal
        },
        { throttleMs: 5000 }
      );
      if (details === "bufferStalledError" || details === "bufferSeekOverHole") {
        attemptMediaRecovery();
        return;
      }
      if (isNetworkLoadFailure || failedSatelliteSegment) {
        if (satelliteAssigned) {
          void switchToAlternateSatellite(`${details || "segment load failed"} status=${responseCode || 0}`);
          return;
        }
        if (hls) {
          hls.destroy();
          hls = null;
        }
        started = false;
        setStatus(false);
      }
      console.log("HLS error:", data);
    });
    return;
  }

  console.log("HLS not supported in this browser.");
};

const handleStatus = (live, audioLiveStatus) => {
  const previousLiveStatus = lastLiveStatus;
  const previousAudioStatus = lastAudioLiveStatus;
  if (typeof audioLiveStatus === "boolean") {
    audioLive = audioLiveStatus;
    audioAvailable = audioLiveStatus;
  }
  lastLiveStatus = !!live;
  if (typeof audioLiveStatus === "boolean") {
    lastAudioLiveStatus = audioLiveStatus;
  }
  if (previousLiveStatus !== lastLiveStatus || previousAudioStatus !== lastAudioLiveStatus) {
    emitClientDebug("status_update", { live: lastLiveStatus, audioLive: lastAudioLiveStatus });
  }
  if (adminMode) {
    setStatus(!!live);
    return;
  }
  if (tabLocked) {
    if (audioOnlyEnabled) {
      setStatus(!!audioLive);
      updateAudioControls();
      return;
    }
    setStatus(!!live);
    return;
  }
  if (tabSuppressed) {
    if (audioOnlyEnabled) {
      setStatus(!!audioLive);
      updateAudioControls();
      return;
    }
    setStatus(!!live);
    return;
  }
  if (audioOnlyEnabled) {
    setStatus(!!audioLive);
    updateAudioControls();
    return;
  }
  if (live) {
    setStatus(true);
    if (!started) {
      if (autostartEnabled) {
        recordAutostartAttempt();
        if (!autostartEnabled) return;
      }
      setActiveMedia();
      if (allowAutoplay && mediaEl) {
        mediaEl.muted = true;
      }
      updateUnmute();
      startPlayer();
    }
    return;
  }
  setStatus(false);
  if (started) {
    stopPlayer();
  }
};

if (!adminMode) {
  initTabControl();
  if (claimOnLoad) {
    claimActiveTab("manual-reload");
  }
}

let statusFailureTimer = null;

const clearStatusFailure = () => {
  if (statusFailureTimer === null) return;
  clearTimeout(statusFailureTimer);
  statusFailureTimer = null;
};

const handleStatusFailure = (reason) => {
  postClientLog("status_poll_failed", { reason });
  if (statusFailureTimer !== null) return;
  statusFailureTimer = setTimeout(() => {
    statusFailureTimer = null;
    audioLive = false;
    audioAvailable = false;
    setStatus(false);
    updateViewerCount(0);
    if (started) {
      stopPlayer();
    }
  }, statusFailureGraceMs);
};

const pollStatus = async () => {
  try {
    const response = await fetch(statusUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status fetch failed: ${response.status}`);
    }
    const data = await response.json();
    clearStatusFailure();
    handleStatus(!!data?.live, data?.audio_live);
    updateViewerCount(data?.count);
  } catch (error) {
    debugLog(`status poll failed: ${error?.message || error}`);
    emitClientDebug(
      "status_poll_failed",
      { message: error?.message || String(error) },
      { throttleMs: 15000, sendHttpFallback: true }
    );
    handleStatusFailure(error?.message || String(error));
  }
};

pollStatus();
setInterval(pollStatus, statusPollIntervalMs);
