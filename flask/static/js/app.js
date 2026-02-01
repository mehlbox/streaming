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
const autostartToggle = document.getElementById("autostart");
const autostartLabel = document.getElementById("autostart-label");
const autostartTextEl = autostartLabel?.querySelector(".autostart-text");
const audioOnlyToggle = document.getElementById("audio-only");
const clientsEl = document.getElementById("clients");
const reloadBtn = document.getElementById("reload");
const scheduleEl = document.getElementById("schedule");
const scheduleEmptyEl = document.getElementById("schedule-empty");
const offlineTitleEl = document.getElementById("offline-title");
const offlineSubEl = document.getElementById("offline-sub");
const statsCanvas = document.getElementById("stats-canvas");
const statsEmptyEl = document.getElementById("stats-empty");
const statsRangeEl = document.querySelector(".stats-range");
const statsSection = document.getElementById("stats-section");
const scheduleLocale = "de-DE";
const scheduleTheme = document.documentElement?.dataset?.theme || "ocean";
const scheduleBaseUrl = (document.body?.dataset?.scheduleBaseUrl || "/data").replace(/\/$/, "");
const scheduleUrl = document.body?.dataset?.scheduleUrl || `${scheduleBaseUrl}/schedule-${scheduleTheme}.json`;
const audioStatusUrl = document.body?.dataset?.audioStatusUrl || "/audio-status";
const audioOnlyForced = document.body?.dataset?.audioOnly === "1";
const timeFormatter = new Intl.DateTimeFormat(scheduleLocale, { hour: "2-digit", minute: "2-digit" });
const dayFormatter = new Intl.DateTimeFormat(scheduleLocale, { day: "2-digit" });
const monthFormatter = new Intl.DateTimeFormat(scheduleLocale, { month: "short" });
const weekdayFormatter = new Intl.DateTimeFormat(scheduleLocale, { weekday: "short" });
const longDateFormatter = new Intl.DateTimeFormat(scheduleLocale, {
  weekday: "long",
  day: "2-digit",
  month: "long"
});
const statsMinutes = 60;
let hls = null;
let started = false;
let isLive = false;
let autostartEnabled = autostartToggle?.checked ?? true;
let audioOnlyEnabled = audioOnlyForced || (audioOnlyToggle?.checked ?? false);
let mediaEl = video;
let activeHlsUrl = hlsUrl;
let mediaRecoveryAttempts = 0;
let lastMediaRecoveryAt = 0;
const audioOnlyStorageKey = "audioOnly";
const autostartAttemptsKey = "autostartAttempts";
const autostartMaxAttempts = 10;
const volumeStorageKey = "audioVolume";
let allowAutoplay = true;
const debugEnabled = document.body?.dataset?.debug === "1";
let scheduleData = [];
let audioAvailable = false;
let audioLive = null;
let playbackActive = false;
let autostartAttempts = 0;

const debugLog = (message) => {
  if (!debugPanel || !debugEnabled) return;
  debugPanel.classList.remove("hidden");
  const ts = new Date().toLocaleTimeString();
  debugPanel.textContent += `[${ts}] ${message}\n`;
  debugPanel.scrollTop = debugPanel.scrollHeight;
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

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

const loadAutostartAttempts = () => {
  const stored = sessionStorage.getItem(autostartAttemptsKey);
  const value = Number.parseInt(stored, 10);
  return Number.isFinite(value) && value >= 0 ? value : 0;
};

const logAutostartStatus = (reason) => {
  if (!debugEnabled) return;
  const state = autostartEnabled ? "on" : "off";
  const suffix = reason ? ` - ${reason}` : "";
  debugLog(`autostart ${state} (${autostartAttempts}/${autostartMaxAttempts})${suffix}`);
};

const setAutostartAttempts = (value, reason) => {
  autostartAttempts = Math.max(0, value);
  sessionStorage.setItem(autostartAttemptsKey, autostartAttempts.toString());
  updateAutostartUI();
  logAutostartStatus(reason);
};

const recordAutostartAttempt = () => {
  if (!autostartEnabled) return;
  const next = autostartAttempts + 1;
  setAutostartAttempts(next, "attempt");
  if (next >= autostartMaxAttempts) {
    autostartEnabled = false;
    if (autostartToggle) autostartToggle.checked = false;
    logAutostartStatus("disabled");
  }
  updateAutostartUI();
};

autostartAttempts = loadAutostartAttempts();
if (autostartAttempts >= autostartMaxAttempts) {
  autostartEnabled = false;
  if (autostartToggle) autostartToggle.checked = false;
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
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event?.reason?.message || String(event?.reason || "Unknown rejection");
  debugLog(`promise: ${reason}`);
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

const renderSchedule = () => {
  if (!scheduleEl) return;
  scheduleEl.innerHTML = "";
  const now = new Date();
  const visibleEntries = scheduleData.filter((entry) => entry.end >= now);
  if (!visibleEntries.length) {
    if (scheduleEmptyEl) scheduleEmptyEl.hidden = false;
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
    offlineSubEl.textContent = "Derzeit ist kein Stream geplant.";
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
  offlineSubEl.textContent = "Derzeit ist kein weiterer Stream geplant.";
};

const refreshScheduleUI = () => {
  renderSchedule();
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
  if (!playerEl) return;
  const audioReady = audioLive && audioAvailable;
  const liveState = audioOnlyEnabled ? audioReady : isLive;
  playerEl.className = "player";
  if (!liveState) playerEl.classList.add("offline-mode");
  if (audioOnlyEnabled) playerEl.classList.add("audio-only");
  if (offlineEl) {
    offlineEl.className = liveState ? "offline hidden" : "offline";
  }
};

const updateAutostartUI = () => {
  if (!autostartLabel) return;
  if (audioOnlyEnabled) {
    autostartLabel.classList.add("hidden");
  } else {
    autostartLabel.classList.remove("hidden");
  }
  if (autostartTextEl) {
    if (!autostartEnabled && autostartAttempts >= autostartMaxAttempts) {
      autostartTextEl.textContent = `Autostart aus (${autostartAttempts}/${autostartMaxAttempts})`;
    } else {
      autostartTextEl.textContent = "Autostart";
    }
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
    activeHlsUrl = audioHlsUrl || "";
  } else {
    activeHlsUrl = hlsUrl;
  }
  updatePlayerClass();
  updateAutostartUI();
  applyVolume();
};

const statsColors = () => {
  const styles = getComputedStyle(document.documentElement);
  return {
    line: styles.getPropertyValue("--wine").trim() || "#8b2b2e",
    fill: styles.getPropertyValue("--wine").trim() || "#8b2b2e",
    grid: styles.getPropertyValue("--stroke").trim() || "rgba(0,0,0,0.12)",
    text: styles.getPropertyValue("--muted").trim() || "#6f6760"
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
  const { line, fill, grid, text } = statsColors();
  const width = statsCanvas.getBoundingClientRect().width;
  const height = statsCanvas.getBoundingClientRect().height;
  ctx.clearRect(0, 0, width, height);
  if (!points || points.length === 0) {
    if (statsEmptyEl) statsEmptyEl.hidden = false;
    return;
  }
  if (statsEmptyEl) statsEmptyEl.hidden = true;

  const now = Date.now() / 1000;
  const minTs = now - statsMinutes * 60;
  const maxTs = now;
  const counts = points.map((p) => p.count);
  const maxCount = Math.max(1, ...counts);

  const padding = 12;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding, height - padding);
  ctx.lineTo(width - padding, height - padding);
  ctx.stroke();

  const toX = (ts) => padding + ((ts - minTs) / (maxTs - minTs)) * chartWidth;
  const toY = (count) => height - padding - (count / maxCount) * chartHeight;

  ctx.beginPath();
  points.forEach((point, index) => {
    const x = toX(Math.min(Math.max(point.ts, minTs), maxTs));
    const y = toY(point.count);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = line;
  ctx.lineWidth = 2;
  ctx.stroke();

  const last = points[points.length - 1];
  const lastX = toX(Math.min(Math.max(last.ts, minTs), maxTs));
  const lastY = toY(last.count);
  ctx.fillStyle = fill;
  ctx.beginPath();
  ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = text;
  ctx.font = "11px \"Space Grotesk\", sans-serif";
  ctx.fillText(`${last.count} Online`, padding, padding + 2);
};

const fetchStats = async () => {
  if (!statsCanvas) return;
  try {
    const response = await fetch(`/stats?minutes=${statsMinutes}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    drawStats(data.points || []);
    if (statsRangeEl) statsRangeEl.textContent = `Letzte ${data.minutes || statsMinutes} Minuten`;
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
  setInterval(fetchStats, 60000);
};

const attemptMediaRecovery = () => {
  if (!hls || !isLive) return;
  const now = Date.now();
  if (now - lastMediaRecoveryAt > 30000) {
    mediaRecoveryAttempts = 0;
  }
  lastMediaRecoveryAt = now;
  mediaRecoveryAttempts += 1;
  if (mediaRecoveryAttempts <= 2) {
    try {
      hls.recoverMediaError();
      jumpToLiveEdge(hls, mediaEl);
      mediaEl?.play().catch(() => {});
    } catch (error) {
      console.log("HLS recovery failed:", error);
    }
    return;
  }
  if (autostartEnabled) {
    forceReload();
  }
  mediaRecoveryAttempts = 0;
};

const forceReload = () => {
  const url = new URL(window.location.href);
  url.searchParams.set("reload", Date.now().toString());
  window.location.replace(url.toString());
};
const url = new URL(window.location.href);
if (url.searchParams.has("reload")) {
  url.searchParams.delete("reload");
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
setInterval(() => {
  const isPlaying = mediaEl && !mediaEl.paused && !mediaEl.ended && mediaEl.readyState > 2;
  if (!audioOnlyEnabled && autostartEnabled && isLive && !isPlaying) {
    forceReload();
  }
}, 10000);
loadSchedule();
setInterval(refreshScheduleUI, 60000);
if (debugEnabled) {
  if (statsSection) statsSection.classList.remove("hidden");
  initStats();
} else if (statsSection) {
  statsSection.classList.add("hidden");
}
document.addEventListener("visibilitychange", syncAudioVisualizer);

if (autostartToggle) {
  autostartToggle.addEventListener("change", () => {
    autostartEnabled = autostartToggle.checked;
    if (autostartEnabled) {
      setAutostartAttempts(0, "user-enable");
    } else {
      logAutostartStatus("user-disable");
    }
    updateAutostartUI();
  });
}

if (audioOnlyToggle) {
  audioOnlyToggle.addEventListener("change", () => {
    setActiveMedia();
    updateAutostartUI();
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

const jumpToLiveEdge = (hlsInstance, element) => {
  const livePos = hlsInstance?.liveSyncPosition;
  if (!element || !livePos) return;
  if (Math.abs(element.currentTime - livePos) > 2) {
    element.currentTime = livePos;
  }
};

const updateUnmute = () => {
  if (!unmuteEl) return;
  if (!mediaEl) return;
  if (audioOnlyEnabled) {
    unmuteEl.className = "unmute hidden";
    updateAudioControls();
    return;
  }
  const isPlaying = started && playbackActive;
  const show = isLive && isPlaying && mediaEl.muted;
  unmuteEl.className = show ? "unmute" : "unmute hidden";
  updateAudioControls();
};

const attemptPlay = (element) => {
  if (!element) return;
  element.play().catch((error) => {
    debugLog(`play() failed: ${error?.message || error}`);
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
  statusEl.textContent = live ? "Online" : "Offline";
  statusEl.className = live ? "status status-online" : "status status-offline";
  updatePlayerClass();
  updateAutostartUI();
  updateOfflineMessage();
  updateUnmute();
  updateAudioControls();
};

if (unmuteBtn) {
  unmuteBtn.addEventListener("click", () => {
    if (!mediaEl) return;
    mediaEl.muted = false;
    attemptPlay(mediaEl);
    allowAutoplay = false;
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
    if (ready === null || socket?.connected === false) {
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
      startPlayerWithOptions({ forcePlay: true });
      updateAudioControls();
      return;
    }
    if (mediaEl.muted || mediaEl.paused || mediaEl.ended) {
      mediaEl.muted = false;
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
    }
    updateAudioControls();
    updateUnmute();
  });
}

[video, audio].forEach((element) => {
  if (!element) return;
  element.addEventListener("volumechange", updateUnmute);
  element.addEventListener("play", updateUnmute);
  element.addEventListener("playing", (event) => {
    if (event.currentTarget !== mediaEl) return;
    playbackActive = true;
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
    if (code === 4 && audioOnlyEnabled && started) {
      audioAvailable = false;
      stopPlayer();
      updateAudioControls();
    }
    if (event.currentTarget === mediaEl) {
      playbackActive = false;
      updateUnmute();
    }
  });
  element.addEventListener("stalled", (event) => {
    debugLog("media stalled");
    if (event.currentTarget === mediaEl) {
      playbackActive = false;
      updateUnmute();
    }
  });
  element.addEventListener("waiting", (event) => {
    debugLog("media waiting");
    if (event.currentTarget === mediaEl) {
      playbackActive = false;
      updateUnmute();
    }
  });
  element.addEventListener("loadedmetadata", () => debugLog("media loadedmetadata"));
  element.addEventListener("canplay", () => debugLog("media canplay"));
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
  started = false;
  playbackActive = false;
  stopAudioVisualizer();
  resetMediaElement(video);
  resetMediaElement(audio);
};

const startPlayer = () => {
  startPlayerWithOptions({ forcePlay: false });
};

const startPlayerWithOptions = ({ forcePlay }) => {
  if (started) return;
  setActiveMedia();
  if (!activeHlsUrl) {
    debugLog("no media source available");
    updateAudioControls();
    return;
  }
  started = true;
  setStatus(true);

  if (mediaEl && mediaEl.canPlayType("application/vnd.apple.mpegurl")) {
    mediaEl.src = activeHlsUrl;
    if (forcePlay) {
      attemptPlay(mediaEl);
    }
    mediaEl.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(mediaEl.duration)) {
        mediaEl.currentTime = Math.max(0, mediaEl.duration - 0.5);
      }
      attemptPlay(mediaEl);
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
      backBufferLength: 30
    });
    hls.loadSource(activeHlsUrl);
    if (mediaEl) {
      hls.attachMedia(mediaEl);
    }
    if (forcePlay) {
      attemptPlay(mediaEl);
    }
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      jumpToLiveEdge(hls, mediaEl);
      attemptPlay(mediaEl);
      setTimeout(updateUnmute, 100);
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      const details = data?.details || "";
      debugLog(`hls error: ${details || data?.type || "unknown"}`);
      if (details === "bufferStalledError" || details === "bufferSeekOverHole") {
        attemptMediaRecovery();
        return;
      }
      if (details === "manifestLoadError" || details === "levelLoadError") {
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
  if (typeof audioLiveStatus === "boolean") {
    audioLive = audioLiveStatus;
    audioAvailable = audioLiveStatus;
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

const socket = io({ transports: ["websocket"] });
socket.on("status", (data) => {
  handleStatus(!!data?.live, data?.audio_live);
});
socket.on("clients", (data) => {
  if (!clientsEl) return;
  const count = Number.isFinite(data?.count) ? data.count : 0;
  clientsEl.textContent = `Aktuell online: ${count}`;
});
socket.on("disconnect", () => {
  audioLive = false;
  audioAvailable = false;
  setStatus(false);
  if (started) {
    stopPlayer();
  }
});
