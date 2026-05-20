import ipaddress
import os
import secrets
import time
import uuid
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

from flask import Flask, abort, jsonify, render_template, request, session, url_for

app = Flask(__name__)
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
app.config["SECRET_KEY"] = SECRET_KEY


def get_env_default(key: str, default: str) -> str:
    value = os.getenv(key, "").strip()
    return value or default


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


STREAM_NAME = os.getenv("STREAM_NAME", "live")
STREAM_KEY = os.getenv("STREAM_KEY", "").strip()
HLS_DIR = os.getenv("HLS_DIR", "/var/www/hls")
HLS_STALE_SECONDS = int(os.getenv("HLS_STALE_SECONDS", "15"))
STATS_SAMPLE_SECONDS = int(os.getenv("STATS_SAMPLE_SECONDS", "60"))
AUDIO_STREAM_NAME = os.getenv("AUDIO_STREAM_NAME", "").strip()
AUDIO_HLS_URL = os.getenv("AUDIO_HLS_URL", "").strip()
AUDIO_ONLY = parse_bool(os.getenv("AUDIO_ONLY", ""))
THEME = os.getenv("THEME", "ocean").strip().lower()
SUPPORTED_THEMES = {"stephanus", "ocean", "midnight", "bethaus"}
if THEME not in SUPPORTED_THEMES:
    THEME = "ocean"
SITE_TITLE = get_env_default("SITE_TITLE", "docker streaming")
SITE_SUBTITLE = get_env_default("SITE_SUBTITLE", "")
PAGE_TITLE = get_env_default("PAGE_TITLE", f"Live Stream - {SITE_TITLE}")
LOGO_URL = get_env_default("LOGO_URL", "")
LOGO_ALT = get_env_default("LOGO_ALT", "Your Logo Here")
FAVICON_URL = os.getenv("FAVICON_URL", "").strip()
FAVICON_TYPE = get_env_default("FAVICON_TYPE", "image/svg+xml")
FOOTER_URL = get_env_default("FOOTER_URL", "")
FOOTER_TEXT = get_env_default("FOOTER_TEXT", "Your Footers Here")
SCHEDULE_BASE_URL = get_env_default("SCHEDULE_BASE_URL", "/static/data")
SATELLITE_API_KEY = os.getenv("SATELLITE_API_KEY", "").strip()
SATELLITE_HEARTBEAT_INTERVAL = int(os.getenv("SATELLITE_HEARTBEAT_INTERVAL", "10"))
SATELLITE_UNHEALTHY_SECONDS = int(os.getenv("SATELLITE_UNHEALTHY_SECONDS", "30"))
SATELLITE_PRUNE_SECONDS = int(os.getenv("SATELLITE_PRUNE_SECONDS", "120"))
VIEWER_PRESENCE_TTL_SECONDS = max(
    30.0,
    float(os.getenv("VIEWER_PRESENCE_TTL_SECONDS", "45")),
)
APP_DEBUG = parse_bool(os.getenv("APP_DEBUG", "0"))

viewer_lock = Lock()
viewer_sessions: dict[str, float] = {}
stats_lock = Lock()
stats_data: dict[int, int] = {}
stats_task_started = False
satellite_lock = Lock()
satellites = {}
satellite_pruner_started = False


def ensure_viewer_id() -> str:
    viewer_id = session.get("viewer_id", "").strip()
    if not viewer_id:
        viewer_id = secrets.token_urlsafe(16)
        session["viewer_id"] = viewer_id
    return viewer_id


def render_index(debug_enabled: bool):
    ensure_viewer_id()
    audio_hls_url = None
    if AUDIO_HLS_URL:
        audio_hls_url = AUDIO_HLS_URL
    elif AUDIO_STREAM_NAME:
        audio_hls_url = f"/hls/{AUDIO_STREAM_NAME}.m3u8"
    favicon_url = FAVICON_URL or url_for("static", filename="favicon.svg")
    return render_template(
        "index.html",
        hls_url=f"/hls/{STREAM_NAME}.m3u8",
        audio_hls_url=audio_hls_url,
        theme=THEME,
        site_title=SITE_TITLE,
        site_subtitle=SITE_SUBTITLE,
        page_title=PAGE_TITLE,
        logo_url=LOGO_URL,
        logo_alt=LOGO_ALT,
        favicon_url=favicon_url,
        favicon_type=FAVICON_TYPE,
        audio_only=AUDIO_ONLY,
        debug_enabled=debug_enabled,
        footer_url=FOOTER_URL,
        footer_text=FOOTER_TEXT,
        schedule_base_url=SCHEDULE_BASE_URL,
    )


@app.get("/")
def index():
    return render_index(False)


@app.get("/debug")
def debug_index():
    return render_index(True)


def is_live() -> bool:
    hls_path = Path(HLS_DIR) / f"{STREAM_NAME}.m3u8"
    if not hls_path.exists():
        return False
    age = time.time() - hls_path.stat().st_mtime
    return age <= HLS_STALE_SECONDS


def is_audio_live() -> bool:
    if AUDIO_HLS_URL:
        return True
    if not AUDIO_STREAM_NAME:
        return False
    hls_path = Path(HLS_DIR) / f"{AUDIO_STREAM_NAME}.m3u8"
    if not hls_path.exists():
        return False
    age = time.time() - hls_path.stat().st_mtime
    return age <= HLS_STALE_SECONDS


def is_private_addr(addr: str) -> bool:
    if not addr:
        return False
    candidate = addr.strip()
    if candidate.startswith("["):
        end = candidate.find("]")
        if end != -1:
            candidate = candidate[1:end]
    elif ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def shorten(value, max_len: int = 240):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def prune_viewers_locked(now: float) -> None:
    cutoff = now - VIEWER_PRESENCE_TTL_SECONDS
    stale = [session_id for session_id, last_seen in viewer_sessions.items() if last_seen < cutoff]
    for session_id in stale:
        viewer_sessions.pop(session_id, None)


def local_viewer_count(now: float | None = None) -> int:
    current = time.time() if now is None else now
    with viewer_lock:
        prune_viewers_locked(current)
        return len(viewer_sessions)


def mark_viewer_present() -> int:
    now = time.time()
    session_id = ensure_viewer_id()
    with viewer_lock:
        prune_viewers_locked(now)
        viewer_sessions[session_id] = now
        return len(viewer_sessions)


@app.get("/status")
def status():
    return jsonify({"live": is_live(), "audio_live": is_audio_live()})


@app.get("/audio-status")
def audio_status():
    return jsonify({"live": is_audio_live()})


@app.get("/presence")
def presence():
    local_count = mark_viewer_present()
    return jsonify(
        {
            "live": is_live(),
            "audio_live": is_audio_live(),
            "count": total_viewer_count(local_count=local_count),
            "local_count": local_count,
        }
    )


def total_viewer_count(local_count: int | None = None) -> int:
    count = local_count if local_count is not None else local_viewer_count()
    now = time.time()
    with satellite_lock:
        for sat in satellites.values():
            if now - sat.get("last_heartbeat", 0) <= SATELLITE_UNHEALTHY_SECONDS:
                count += sat.get("viewer_count", 0)
    return count


def record_stats() -> None:
    while True:
        count = total_viewer_count()
        ts = int(time.time())
        cutoff = ts - 60 * 60 * 24
        with stats_lock:
            stats_data[ts] = count
            for old_ts in [k for k in stats_data if k < cutoff]:
                del stats_data[old_ts]
        time.sleep(STATS_SAMPLE_SECONDS)


def ensure_stats_task() -> None:
    global stats_task_started
    if stats_task_started:
        return
    with stats_lock:
        if stats_task_started:
            return
        stats_task_started = True
        Thread(target=record_stats, daemon=True).start()


@app.post("/auth")
def auth():
    call = request.values.get("call", "")
    stream_name = request.values.get("name", "")
    token = request.values.get("key")
    if not token:
        args = request.values.get("args", "")
        token = parse_qs(args).get("key", [None])[0]
    if not token:
        tcurl = request.values.get("tcurl", "")
        query = urlparse(tcurl).query
        token = parse_qs(query).get("key", [None])[0]
    if not token:
        qs = request.query_string.decode("utf-8", errors="ignore")
        token = parse_qs(qs).get("key", [None])[0]
    if not token:
        raw = request.get_data(as_text=True) or ""
        token = parse_qs(raw).get("key", [None])[0]

    if call == "connect":
        if not STREAM_KEY or token == STREAM_KEY:
            return "OK"
        addr = request.values.get("addr", "")
        if is_private_addr(addr):
            return "OK"
        abort(403)
    if call == "publish":
        if stream_name == STREAM_NAME and (not STREAM_KEY or token == STREAM_KEY):
            return "OK"
        abort(403)
    if not STREAM_KEY or token == STREAM_KEY:
        return "OK"
    abort(403)


@app.post("/client-log")
def client_log():
    return ("ok", 200)


@app.get("/stats")
def stats():
    minutes = request.args.get("minutes", "60")
    try:
        minutes_int = max(1, min(240, int(minutes)))
    except ValueError:
        minutes_int = 60
    cutoff = int(time.time()) - minutes_int * 60
    with stats_lock:
        rows = sorted((ts, count) for ts, count in stats_data.items() if ts >= cutoff)
    return jsonify(
        {
            "points": [{"ts": ts, "count": count} for ts, count in rows],
            "minutes": minutes_int,
        }
    )


# ---------------------------------------------------------------------------
# Satellite management
# ---------------------------------------------------------------------------


def validate_satellite_api_key():
    if not SATELLITE_API_KEY:
        abort(403, "Satellite API not configured")
    data = request.get_json(silent=True) or {}
    key = data.get("api_key", "")
    if not isinstance(key, str) or not secrets.compare_digest(key, SATELLITE_API_KEY):
        abort(403, "Invalid API key")
    return data


def satellite_score(sat):
    now = time.time()
    if now - sat.get("last_heartbeat", 0) > SATELLITE_UNHEALTHY_SECONDS:
        return -1
    capacity = sat.get("capacity_max_viewers", 100)
    viewers = sat.get("viewer_count", 0)
    cpu = sat.get("cpu_percent", 0)
    headroom = max(0, capacity - viewers)
    return headroom * (1 - min(cpu, 100) / 100)


def satellite_info(sat, now=None):
    if now is None:
        now = time.time()
    age = round(now - sat.get("last_heartbeat", now), 1)
    healthy = age <= SATELLITE_UNHEALTHY_SECONDS
    return {
        "id": sat["id"],
        "name": sat.get("name", ""),
        "url": sat.get("url", ""),
        "viewer_count": sat.get("viewer_count", 0),
        "cpu_percent": sat.get("cpu_percent", 0),
        "bandwidth_mbps": sat.get("bandwidth_mbps", 0),
        "capacity_max_viewers": sat.get("capacity_max_viewers", 100),
        "last_heartbeat_age": age,
        "healthy": healthy,
    }


def prune_stale_satellites():
    while True:
        now = time.time()
        cutoff = now - SATELLITE_PRUNE_SECONDS
        with satellite_lock:
            stale = [sid for sid, s in satellites.items() if s.get("last_heartbeat", 0) < cutoff]
            for sid in stale:
                satellites.pop(sid, None)
                app.logger.info("Pruned stale satellite %s", sid)
        time.sleep(15)


def ensure_satellite_pruner():
    global satellite_pruner_started
    if satellite_pruner_started:
        return
    with satellite_lock:
        if satellite_pruner_started:
            return
        satellite_pruner_started = True
        Thread(target=prune_stale_satellites, daemon=True).start()


@app.post("/api/satellite/register")
def satellite_register():
    data = validate_satellite_api_key()
    name = shorten(data.get("name", ""), 120)
    url = shorten(data.get("url", ""), 500)
    if not url:
        abort(400, "Missing satellite url")
    sat_id = str(uuid.uuid4())
    now = time.time()
    sat = {
        "id": sat_id,
        "name": name,
        "url": url,
        "cpu_percent": 0,
        "bandwidth_mbps": 0,
        "viewer_count": 0,
        "capacity_max_viewers": int(data.get("capacity_max_viewers", 100)),
        "last_heartbeat": now,
        "registered_at": now,
    }
    with satellite_lock:
        satellites[sat_id] = sat
    ensure_satellite_pruner()
    app.logger.info("Satellite registered: %s (%s) at %s", sat_id, name, url)
    return jsonify(
        {
            "id": sat_id,
            "heartbeat_interval": SATELLITE_HEARTBEAT_INTERVAL,
        }
    )


@app.post("/api/satellite/<sat_id>/heartbeat")
def satellite_heartbeat(sat_id):
    data = validate_satellite_api_key()
    with satellite_lock:
        sat = satellites.get(sat_id)
        if not sat:
            abort(404, "Unknown satellite")
        sat["cpu_percent"] = min(100, max(0, float(data.get("cpu_percent", 0))))
        sat["bandwidth_mbps"] = max(0, float(data.get("bandwidth_mbps", 0)))
        sat["viewer_count"] = max(0, int(data.get("viewer_count", 0)))
        sat["capacity_max_viewers"] = max(1, int(data.get("capacity_max_viewers", sat["capacity_max_viewers"])))
        sat["last_heartbeat"] = time.time()
    return jsonify({"ok": True, "stream_active": is_live()})


@app.delete("/api/satellite/<sat_id>")
def satellite_deregister(sat_id):
    validate_satellite_api_key()
    with satellite_lock:
        removed = satellites.pop(sat_id, None)
    if removed:
        app.logger.info("Satellite deregistered: %s (%s)", sat_id, removed.get("name", ""))
    return jsonify({"ok": True})


@app.get("/api/satellite/assign")
def satellite_assign():
    best = None
    best_score = -1
    with satellite_lock:
        for sat in satellites.values():
            score = satellite_score(sat)
            if score > best_score:
                best_score = score
                best = sat
    if best and best_score > 0:
        return jsonify({"satellite_url": best["url"]})
    return jsonify({"satellite_url": None})


@app.get("/api/satellites")
def satellite_list():
    now = time.time()
    with satellite_lock:
        result = [satellite_info(s, now) for s in satellites.values()]
    return jsonify({"satellites": result})


if __name__ == "__main__":
    ensure_stats_task()
    ensure_satellite_pruner()
    app.run(host="0.0.0.0", port=5000, debug=APP_DEBUG, use_reloader=False, threaded=True)
