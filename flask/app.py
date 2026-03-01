import os
import ipaddress
import sqlite3
import time
import secrets
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from threading import Lock
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, abort, jsonify, render_template, request, session, url_for
from flask_socketio import SocketIO, emit

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
LOGO_ALT = get_env_default("LOGO_ALT", "Your Logo Here")
FAVICON_URL = os.getenv("FAVICON_URL", "").strip()
FAVICON_TYPE = get_env_default("FAVICON_TYPE", "image/svg+xml")
FOOTER_URL = get_env_default("FOOTER_URL", "")
FOOTER_TEXT = get_env_default("FOOTER_TEXT", "Your Footers Here")
SCHEDULE_BASE_URL = get_env_default("SCHEDULE_BASE_URL", "/static/data")
DISCONNECT_LOG_ENABLED = parse_bool(os.getenv("DISCONNECT_LOG_ENABLED", "1"))
DISCONNECT_LOG_PATH = os.getenv(
    "DISCONNECT_LOG_PATH",
    "/docker/streaming/flask/disconnect-debug.log",
).strip()
DISCONNECT_LOG_MAX_BYTES = int(os.getenv("DISCONNECT_LOG_MAX_BYTES", "5242880"))
DISCONNECT_LOG_BACKUP_COUNT = int(os.getenv("DISCONNECT_LOG_BACKUP_COUNT", "10"))
socketio = SocketIO(app, cors_allowed_origins="*")
client_lock = Lock()
client_count = 0
client_sessions = {}
sid_to_session = {}
sid_meta = {}
stats_lock = Lock()
stats_task_started = False

disconnect_logger = logging.getLogger("disconnect_debug")
disconnect_logger.setLevel(logging.INFO)
disconnect_logger.propagate = False
if DISCONNECT_LOG_ENABLED:
    try:
        log_path = Path(DISCONNECT_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        disconnect_handler = RotatingFileHandler(
            log_path,
            maxBytes=max(1024, DISCONNECT_LOG_MAX_BYTES),
            backupCount=max(1, DISCONNECT_LOG_BACKUP_COUNT),
            encoding="utf-8",
        )
        disconnect_handler.setFormatter(logging.Formatter("%(message)s"))
        disconnect_logger.addHandler(disconnect_handler)
    except Exception:
        app.logger.exception("Failed to initialize disconnect debug logger")

def render_index(debug_enabled: bool):
    if "viewer_id" not in session:
        session["viewer_id"] = secrets.token_urlsafe(16)
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


def shorten(value, max_len: int = 240):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.headers.get("X-Real-IP", "") or request.remote_addr or ""


def session_cookie_name() -> str:
    return app.config.get("SESSION_COOKIE_NAME", "session")


def current_session_id() -> str:
    return session.get("viewer_id") or request.cookies.get(session_cookie_name()) or ""


def sanitize_payload(payload, depth: int = 0):
    if depth > 3:
        return shorten(payload, 120)
    if payload is None:
        return None
    if isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        return shorten(payload, 500)
    if isinstance(payload, dict):
        cleaned = {}
        for key in list(payload.keys())[:20]:
            cleaned[shorten(key, 64)] = sanitize_payload(payload[key], depth + 1)
        return cleaned
    if isinstance(payload, list):
        return [sanitize_payload(item, depth + 1) for item in payload[:20]]
    return shorten(repr(payload), 500)


def log_disconnect_event(event: str, sid: str = "", session_id: str = "", **fields) -> None:
    if not DISCONNECT_LOG_ENABLED:
        return
    if not disconnect_logger.handlers:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    if sid:
        record["sid"] = sid
    if session_id:
        record["session_id"] = session_id
    record.update({k: sanitize_payload(v) for k, v in fields.items()})
    try:
        disconnect_logger.info(json.dumps(record, sort_keys=True, separators=(",", ":")))
    except Exception:
        app.logger.exception("Failed to write disconnect debug log")


@app.get("/status")
def status():
    return jsonify({"live": is_live(), "audio_live": is_audio_live()})

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
            log_disconnect_event(
                "auth_connect_ok",
                call=call,
                stream=stream_name,
                remote_ip=client_ip(),
                addr=shorten(request.values.get("addr", ""), 100),
            )
            return "OK"
        addr = request.values.get("addr", "")
        if is_private_addr(addr):
            log_disconnect_event(
                "auth_connect_ok_private",
                call=call,
                stream=stream_name,
                remote_ip=client_ip(),
                addr=shorten(addr, 100),
            )
            return "OK"
        log_disconnect_event(
            "auth_connect_rejected",
            call=call,
            stream=stream_name,
            remote_ip=client_ip(),
            addr=shorten(addr, 100),
            user_agent=shorten(request.headers.get("User-Agent", ""), 200),
        )
        abort(403)
    if call == "publish":
        if stream_name == STREAM_NAME:
            log_disconnect_event(
                "auth_publish_ok",
                call=call,
                stream=stream_name,
                remote_ip=client_ip(),
            )
            return "OK"
        log_disconnect_event(
            "auth_publish_rejected",
            call=call,
            stream=stream_name,
            remote_ip=client_ip(),
        )
        abort(403)
    if not STREAM_KEY or token == STREAM_KEY:
        log_disconnect_event(
            "auth_generic_ok",
            call=call,
            stream=stream_name,
            remote_ip=client_ip(),
        )
        return "OK"
    log_disconnect_event(
        "auth_generic_rejected",
        call=call,
        stream=stream_name,
        remote_ip=client_ip(),
    )
    abort(403)


@socketio.on("connect")
def on_connect():
    global client_count
    ensure_stats_task()
    emit("status", {"live": is_live(), "audio_live": is_audio_live()})
    session_id = current_session_id()
    if not session_id:
        session_id = f"sid:{request.sid}"
    sid = request.sid
    with client_lock:
        sid_to_session[sid] = session_id
        sid_meta[sid] = {
            "connected_at": time.time(),
            "remote_ip": client_ip(),
            "user_agent": shorten(request.headers.get("User-Agent", ""), 200),
            "origin": shorten(request.headers.get("Origin", ""), 160),
            "referer": shorten(request.headers.get("Referer", ""), 200),
            "transport": shorten(request.args.get("transport", ""), 64),
            "last_client_event": None,
            "last_client_event_at": None,
        }
        session_sockets = client_sessions.setdefault(session_id, set())
        was_empty = len(session_sockets) == 0
        session_sockets.add(sid)
        if was_empty:
            client_count += 1
        count = client_count
    log_disconnect_event(
        "socket_connect",
        sid=sid,
        session_id=session_id,
        remote_ip=sid_meta[sid].get("remote_ip", ""),
        user_agent=sid_meta[sid].get("user_agent", ""),
        transport=sid_meta[sid].get("transport", ""),
        clients=count,
    )
    socketio.emit("clients", {"count": count})


@socketio.on("disconnect")
def on_disconnect(reason=None):
    global client_count
    session_id = ""
    event_meta = {}
    duration_seconds = None
    seconds_since_client_event = None
    with client_lock:
        sid = request.sid
        session_id = sid_to_session.pop(sid, None)
        event_meta = sid_meta.pop(sid, {})
        connected_at = event_meta.get("connected_at")
        last_client_event_at = event_meta.get("last_client_event_at")
        if isinstance(connected_at, (float, int)):
            duration_seconds = round(max(0.0, time.time() - connected_at), 3)
        if isinstance(last_client_event_at, (float, int)):
            seconds_since_client_event = round(max(0.0, time.time() - last_client_event_at), 3)
        if session_id in client_sessions:
            session_sockets = client_sessions.get(session_id, set())
            session_sockets.discard(sid)
            if not session_sockets:
                client_sessions.pop(session_id, None)
                client_count = max(0, client_count - 1)
        count = client_count
    log_disconnect_event(
        "socket_disconnect",
        sid=sid,
        session_id=session_id or "",
        reason=shorten(reason, 80),
        duration_seconds=duration_seconds,
        last_client_event=event_meta.get("last_client_event"),
        seconds_since_client_event=seconds_since_client_event,
        transport=event_meta.get("transport"),
        remote_ip=event_meta.get("remote_ip"),
        user_agent=event_meta.get("user_agent"),
        clients=count,
    )
    socketio.emit("clients", {"count": count})


@socketio.on("client_debug")
def on_client_debug(payload):
    if not isinstance(payload, dict):
        return
    sid = request.sid
    with client_lock:
        session_id = sid_to_session.get(sid, "")
        meta = sid_meta.get(sid)
        if meta is not None:
            meta["last_client_event"] = shorten(payload.get("event", "unknown"), 80)
            meta["last_client_event_at"] = time.time()
    log_disconnect_event(
        "client_debug",
        sid=sid,
        session_id=session_id,
        client_event=shorten(payload.get("event", "unknown"), 80),
        details=sanitize_payload(payload.get("details", {})),
        media=sanitize_payload(payload.get("media", {})),
    )


@app.post("/client-log")
def client_log():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    log_disconnect_event(
        "client_http_log",
        session_id=current_session_id(),
        client_event=shorten(payload.get("event", "unknown"), 80),
        details=sanitize_payload(payload.get("details", {})),
        media=sanitize_payload(payload.get("media", {})),
        remote_ip=client_ip(),
        user_agent=shorten(request.headers.get("User-Agent", ""), 200),
    )
    return ("", 204)


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
    last_live = None
    last_audio = None
    while True:
        live = is_live()
        audio_live = is_audio_live()
        if live != last_live or audio_live != last_audio:
            log_disconnect_event(
                "status_change",
                live=live,
                audio_live=audio_live,
                stream=STREAM_NAME,
                audio_stream=AUDIO_STREAM_NAME,
            )
            socketio.emit("status", {"live": live, "audio_live": audio_live})
            last_live = live
            last_audio = audio_live
        socketio.sleep(SOCKETIO_POLL_SECONDS)


if __name__ == "__main__":
    socketio.start_background_task(status_watcher)
    ensure_stats_task()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
