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
const debugPanel = document.getElementById("debug-panel");
const autostartToggle = document.getElementById("autostart");
const autostartLabel = document.getElementById("autostart-label");
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
const schedule = [
  { date: "2026-02-01", time: "10:00", title: "Eröffnungsgottesdienst", durationMinutes: 90 },
  { date: "2026-02-01", time: "19:30", title: "Abendimpuls", durationMinutes: 60 },
  { date: "2026-02-02", time: "12:00", title: "Mittagsplenum", durationMinutes: 90 },
  { date: "2026-02-03", time: "19:00", title: "Abschlussabend", durationMinutes: 90 }
];
const scheduleLocale = "de-DE";
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
let audioOnlyEnabled = audioOnlyToggle?.checked ?? false;
let mediaEl = video;
let activeHlsUrl = hlsUrl;
let mediaRecoveryAttempts = 0;
let lastMediaRecoveryAt = 0;
const audioOnlyStorageKey = "audioOnly";
let allowAutoplay = true;
const debugEnabled = new URL(window.location.href).searchParams.has("debug");

const debugLog = (message) => {
  if (!debugPanel || !debugEnabled) return;
  debugPanel.classList.remove("hidden");
  const ts = new Date().toLocaleTimeString();
  debugPanel.textContent += `[${ts}] ${message}\n`;
  debugPanel.scrollTop = debugPanel.scrollHeight;
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
  const [year, month, day] = entry.date.split("-").map(Number);
  const [hour, minute] = entry.time.split(":").map(Number);
  return new Date(year, month - 1, day, hour, minute);
};

const isSameDay = (a, b) => (
  a.getFullYear() === b.getFullYear()
  && a.getMonth() === b.getMonth()
  && a.getDate() === b.getDate()
);

const scheduleData = schedule
  .map((entry) => {
    const start = toDate(entry);
    const duration = Number.isFinite(entry.durationMinutes) ? entry.durationMinutes : 90;
    const end = new Date(start.getTime() + duration * 60000);
    return { ...entry, start, end, durationMinutes: duration };
  })
  .sort((a, b) => a.start - b.start);

const renderSchedule = () => {
  if (!scheduleEl) return;
  scheduleEl.innerHTML = "";
  if (!scheduleData.length) {
    if (scheduleEmptyEl) scheduleEmptyEl.hidden = false;
    return;
  }
  if (scheduleEmptyEl) scheduleEmptyEl.hidden = true;
  const now = new Date();
  scheduleData.forEach((entry) => {
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

const updatePlayerClass = () => {
  if (!playerEl) return;
  playerEl.className = "player";
  if (!isLive) playerEl.classList.add("offline-mode");
  if (audioOnlyEnabled) playerEl.classList.add("audio-only");
};

const updateAutostartUI = () => {
  if (!autostartLabel) return;
  if (audioOnlyEnabled) {
    autostartLabel.classList.add("is-hidden");
  } else {
    autostartLabel.classList.remove("is-hidden");
  }
};

const setActiveMedia = () => {
  const wantsAudioOnly = audioOnlyToggle?.checked ?? audioOnlyEnabled;
  if (wantsAudioOnly && !audioHlsUrl) {
    audioOnlyEnabled = false;
    if (audioOnlyToggle) audioOnlyToggle.checked = false;
    sessionStorage.removeItem(audioOnlyStorageKey);
  } else {
    audioOnlyEnabled = wantsAudioOnly;
    if (audioOnlyToggle) {
      sessionStorage.setItem(audioOnlyStorageKey, audioOnlyEnabled ? "1" : "0");
    }
  }
  mediaEl = audioOnlyEnabled ? audio : video;
  activeHlsUrl = audioOnlyEnabled && audioHlsUrl ? audioHlsUrl : hlsUrl;
  updatePlayerClass();
  updateAutostartUI();
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
  ctx.fillText(`${last.count} Zuschauer`, padding, padding + 2);
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
  const storedAudioOnly = sessionStorage.getItem(audioOnlyStorageKey);
  if (storedAudioOnly === "1") {
    audioOnlyToggle.checked = true;
  } else if (storedAudioOnly === "0") {
    audioOnlyToggle.checked = false;
  }
  audioOnlyEnabled = audioOnlyToggle.checked;
}
setActiveMedia();
setInterval(() => {
  const isPlaying = mediaEl && !mediaEl.paused && !mediaEl.ended && mediaEl.readyState > 2;
  if (!audioOnlyEnabled && autostartEnabled && isLive && !isPlaying) {
    forceReload();
  }
}, 10000);
refreshScheduleUI();
setInterval(refreshScheduleUI, 60000);
initStats();

if (autostartToggle) {
  autostartToggle.addEventListener("change", () => {
    autostartEnabled = autostartToggle.checked;
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
  const isPlaying = !mediaEl.paused && !mediaEl.ended;
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
  if (!audioOnlyEnabled || !isLive || !mediaEl) {
    audioControlsEl.className = "audio-controls hidden";
    return;
  }
  const isPlaying = !mediaEl.paused && !mediaEl.ended;
  const isAudible = isPlaying && !mediaEl.muted;
  audioControlsEl.className = isAudible ? "audio-controls playing" : "audio-controls idle";
  if (audioLabelEl) {
    audioLabelEl.textContent = isAudible ? "Audio pausieren" : "Audio starten";
  }
  if (audioTogglePath) {
    audioTogglePath.setAttribute(
      "d",
      isAudible ? "M6 5h4v14H6zm8 0h4v14h-4z" : "M8 5v14l11-7z"
    );
  }
};

const setStatus = (live) => {
  if (!statusEl) return;
  isLive = live;
  statusEl.textContent = live ? "Online" : "Offline";
  statusEl.className = live ? "status status-online" : "status status-offline";
  if (offlineEl) {
    offlineEl.className = live ? "offline hidden" : "offline";
  }
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
  audioToggleBtn.addEventListener("click", () => {
    if (!audioOnlyEnabled || !mediaEl) return;
    if (!isLive) return;
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

[video, audio].forEach((element) => {
  if (!element) return;
  element.addEventListener("volumechange", updateUnmute);
  element.addEventListener("play", updateUnmute);
  element.addEventListener("playing", updateUnmute);
  element.addEventListener("pause", updateAudioControls);
  element.addEventListener("error", (event) => {
    const err = event?.currentTarget?.error;
    const code = err?.code ?? "unknown";
    debugLog(`media error: code=${code}`);
    if (code === 4 && audioOnlyEnabled && started) {
      stopPlayer();
      updateAudioControls();
    }
  });
  element.addEventListener("stalled", () => debugLog("media stalled"));
  element.addEventListener("waiting", () => debugLog("media waiting"));
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
  resetMediaElement(video);
  resetMediaElement(audio);
};

const startPlayer = () => {
  startPlayerWithOptions({ forcePlay: false });
};

const startPlayerWithOptions = ({ forcePlay }) => {
  if (started) return;
  setActiveMedia();
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

const handleStatus = (live) => {
  if (live) {
    setStatus(true);
    if (audioOnlyEnabled) {
      updateAudioControls();
      return;
    }
    if (!started) {
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
  handleStatus(!!data?.live);
});
socket.on("clients", (data) => {
  if (!clientsEl) return;
  const count = Number.isFinite(data?.count) ? data.count : 0;
  clientsEl.textContent = `Aktuelle Zuschauer: ${count}`;
});
socket.on("disconnect", () => {
  setStatus(false);
  if (started) {
    stopPlayer();
  }
});
