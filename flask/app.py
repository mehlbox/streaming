import os
import ipaddress
import sqlite3
import time
from threading import Lock
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, abort, jsonify, render_template, request, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)

def get_env_default(key: str, default: str) -> str:
    value = os.getenv(key, "").strip()
    return value or default

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

STREAM_NAME = os.getenv("STREAM_NAME", "live")
STREAM_KEY = os.getenv("STREAM_KEY", "").strip()
HLS_DIR = os.getenv("HLS_DIR", "/var/www/hls")
HLS_STALE_SECONDS = int(os.getenv("HLS_STALE_SECONDS", "15"))
SOCKETIO_POLL_SECONDS = float(os.getenv("SOCKETIO_POLL_SECONDS", "2"))
STATS_DB = os.getenv("STATS_DB", "/docker/streaming/flask/stats.db")
STATS_SAMPLE_SECONDS = int(os.getenv("STATS_SAMPLE_SECONDS", "60"))
AUDIO_STREAM_NAME = os.getenv("AUDIO_STREAM_NAME", "").strip()
AUDIO_HLS_URL = os.getenv("AUDIO_HLS_URL", "").strip()
AUDIO_ONLY = parse_bool(os.getenv("AUDIO_ONLY", ""))
THEME = os.getenv("THEME", "ocean").strip().lower()
SUPPORTED_THEMES = {"stephanus", "ocean", "midnight", "bethaus"}
if THEME not in SUPPORTED_THEMES:
    THEME = "ocean"
SITE_TITLE = get_env_default("SITE_TITLE", "docker streaming")
PAGE_TITLE = get_env_default("PAGE_TITLE", f"Live Stream - {SITE_TITLE}")
LOGO_URL = get_env_default("LOGO_URL","",)
LOGO_ALT = get_env_default("LOGO_ALT", "CDH Stephanus Logo")
FAVICON_URL = os.getenv("FAVICON_URL", "").strip()
FAVICON_TYPE = get_env_default("FAVICON_TYPE", "image/svg+xml")
FOOTER_URL = get_env_default("FOOTER_URL", "https://cdh-stephanus.org/")
FOOTER_TEXT = get_env_default("FOOTER_TEXT", "CDH Stephanus")
SCHEDULE_BASE_URL = get_env_default("SCHEDULE_BASE_URL", "/static/data")
socketio = SocketIO(app, cors_allowed_origins="*")
client_lock = Lock()
client_count = 0
stats_lock = Lock()
stats_task_started = False

def render_index(debug_enabled: bool):
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


@app.get("/status")
def status():
    return jsonify({"live": is_live()})

@app.get("/audio-status")
def audio_status():
    return jsonify({"live": is_audio_live()})


def init_stats_db() -> None:
    db_path = Path(STATS_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stats (ts INTEGER PRIMARY KEY, count INTEGER NOT NULL)"
        )
        conn.commit()


def record_stats() -> None:
    init_stats_db()
    while True:
        with client_lock:
            count = client_count
        ts = int(time.time())
        with stats_lock, sqlite3.connect(STATS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stats (ts, count) VALUES (?, ?)",
                (ts, count),
            )
            cutoff = ts - 60 * 60 * 24
            conn.execute("DELETE FROM stats WHERE ts < ?", (cutoff,))
            conn.commit()
        socketio.sleep(STATS_SAMPLE_SECONDS)


def ensure_stats_task() -> None:
    global stats_task_started
    if stats_task_started:
        return
    with stats_lock:
        if stats_task_started:
            return
        stats_task_started = True
        socketio.start_background_task(record_stats)


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
        if stream_name == STREAM_NAME:
            return "OK"
        abort(403)
    if not STREAM_KEY or token == STREAM_KEY:
        return "OK"
    abort(403)


@socketio.on("connect")
def on_connect():
    global client_count
    ensure_stats_task()
    emit("status", {"live": is_live()})
    with client_lock:
        client_count += 1
        count = client_count
    socketio.emit("clients", {"count": count})


@socketio.on("disconnect")
def on_disconnect():
    global client_count
    with client_lock:
        client_count = max(0, client_count - 1)
        count = client_count
    socketio.emit("clients", {"count": count})


@app.get("/stats")
def stats():
    init_stats_db()
    minutes = request.args.get("minutes", "60")
    try:
        minutes_int = max(1, min(240, int(minutes)))
    except ValueError:
        minutes_int = 60
    cutoff = int(time.time()) - minutes_int * 60
    with stats_lock, sqlite3.connect(STATS_DB) as conn:
        rows = conn.execute(
            "SELECT ts, count FROM stats WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
    return jsonify(
        {
            "points": [{"ts": ts, "count": count} for ts, count in rows],
            "minutes": minutes_int,
        }
    )


def status_watcher():
    last = None
    while True:
        live = is_live()
        if live != last:
            socketio.emit("status", {"live": live})
            last = live
        socketio.sleep(SOCKETIO_POLL_SECONDS)


if __name__ == "__main__":
    socketio.start_background_task(status_watcher)
    ensure_stats_task()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
