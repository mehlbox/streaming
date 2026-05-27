import ipaddress
import os
import secrets
import re
import sqlite3
import time
import uuid
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from flask import Flask, Response, abort, jsonify, make_response, render_template, request, url_for

def get_env_default(key: str, default: str) -> str:
    value = os.getenv(key, "").strip()
    return value or default


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_required_secret_key() -> str:
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY is required. Set a stable random value in .env before starting the app."
        )
    if len(secret_key) < 32:
        raise RuntimeError(
            "SECRET_KEY is too short. Use at least 32 characters to keep sessions stable and secure."
        )
    return secret_key


app = Flask(__name__)
app.config["SECRET_KEY"] = get_required_secret_key()


STREAM_NAME = os.getenv("STREAM_NAME", "live")
STREAM_KEY = os.getenv("STREAM_KEY", "").strip()
HLS_DIR = os.getenv("HLS_DIR", "/var/www/hls")
HLS_STALE_SECONDS = int(os.getenv("HLS_STALE_SECONDS", "15"))
STATE_DB = os.getenv("STATE_DB", "/app/state.db").strip() or "/app/state.db"
STATE_CACHE_SECONDS = max(
    5.0,
    float(os.getenv("STATE_CACHE_SECONDS", "10")),
)
STATS_SAMPLE_SECONDS = int(os.getenv("STATS_SAMPLE_SECONDS", "60"))
HLS_ACCESS_LOG = os.getenv("HLS_ACCESS_LOG", "").strip()
HLS_VIEWER_WINDOW = max(
    5.0,
    float(os.getenv("HLS_VIEWER_WINDOW", "15")),
)
HLS_LOG_TAIL_BYTES = max(
    65536,
    int(os.getenv("HLS_LOG_TAIL_BYTES", str(1024 * 1024))),
)
NGINX_STATUS_URL = os.getenv("NGINX_STATUS_URL", "").strip()
HLS_VIEWER_COOKIE = "stream_viewer"
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
SATELLITE_BOOTSTRAP_TOKEN = os.getenv("SATELLITE_BOOTSTRAP_TOKEN", "").strip()
SATELLITE_HEARTBEAT_INTERVAL = int(os.getenv("SATELLITE_HEARTBEAT_INTERVAL", "10"))
SATELLITE_UNHEALTHY_SECONDS = int(os.getenv("SATELLITE_UNHEALTHY_SECONDS", "30"))
SATELLITE_PRUNE_SECONDS = int(os.getenv("SATELLITE_PRUNE_SECONDS", "120"))
LOCAL_SATELLITE_NAME = get_env_default("LOCAL_SATELLITE_NAME", "main")
SATELLITE_BOOTSTRAP_INSTALL_DIR = (
    os.getenv("SATELLITE_BOOTSTRAP_INSTALL_DIR", "/opt/streaming-satellite").strip()
    or "/opt/streaming-satellite"
)
SATELLITE_BOOTSTRAP_DIR = Path(
    os.getenv("SATELLITE_BOOTSTRAP_DIR", "/bootstrap/satellite").strip() or "/bootstrap/satellite"
)
APP_DEBUG = parse_bool(os.getenv("APP_DEBUG", "0"))

db_lock = Lock()
db_ready = False
state_lock = Lock()
state_snapshot = {
    "live": False,
    "audio_live": False,
    "count": 0,
    "local_count": 0,
}
state_task_started = False
maintenance_task_started = False


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(STATE_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    global db_ready
    if db_ready:
        return
    with db_lock:
        if db_ready:
            return
        db_path = Path(STATE_DB)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect_db() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS viewer_stats (
                    ts INTEGER PRIMARY KEY,
                    count INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS satellites (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    cpu_percent REAL NOT NULL DEFAULT 0,
                    bandwidth_mbps REAL NOT NULL DEFAULT 0,
                    viewer_count INTEGER NOT NULL DEFAULT 0,
                    capacity_max_viewers INTEGER NOT NULL DEFAULT 100,
                    last_heartbeat REAL NOT NULL,
                    registered_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_satellites_last_heartbeat "
                "ON satellites(last_heartbeat)"
            )
        db_ready = True

def render_index(debug_enabled: bool):
    audio_hls_url = None
    if AUDIO_HLS_URL:
        audio_hls_url = AUDIO_HLS_URL
    elif AUDIO_STREAM_NAME:
        audio_hls_url = f"/hls/{AUDIO_STREAM_NAME}.m3u8"
    favicon_url = FAVICON_URL or url_for("static", filename="favicon.svg")
    response = make_response(
        render_template(
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
    )
    if not request.cookies.get(HLS_VIEWER_COOKIE, "").strip():
        response.set_cookie(
            HLS_VIEWER_COOKIE,
            secrets.token_urlsafe(18),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=not APP_DEBUG,
            samesite="Lax",
            path="/",
        )
    return response


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


def shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def request_external_base_url() -> str:
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",", 1)[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0].strip()
    return f"{scheme or request.scheme}://{host or request.host}".rstrip("/")


def require_http_url(value: str, field_name: str, allow_path: bool) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        abort(400, f"Invalid {field_name}")
    if parsed.query or parsed.fragment:
        abort(400, f"Invalid {field_name}")
    path = parsed.path.rstrip("/")
    if not allow_path and path:
        abort(400, f"Invalid {field_name}")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_satellite_public_url(value: str) -> str:
    base = require_http_url((value or "").strip(), "public_url", allow_path=True)
    if base.endswith("/hls"):
        return base
    parsed = urlparse(base)
    if parsed.path not in {"", "/"}:
        abort(400, "public_url must point to the satellite /hls base")
    return f"{parsed.scheme}://{parsed.netloc}/hls"


def bootstrap_file_text(name: str) -> str:
    path = SATELLITE_BOOTSTRAP_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        abort(500, f"Missing bootstrap asset: {name}")


def render_bootstrap_asset(name: str, replacements: dict[str, str]) -> str:
    text = bootstrap_file_text(name)
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def validate_satellite_bootstrap_token() -> None:
    configured = SATELLITE_BOOTSTRAP_TOKEN or SATELLITE_API_KEY
    if not configured:
        abort(403, "Satellite bootstrap not configured")
    token = request.args.get("token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token or not secrets.compare_digest(token, configured):
        abort(403, "Invalid bootstrap token")


def render_satellite_bootstrap_script() -> str:
    validate_satellite_bootstrap_token()
    if not SATELLITE_API_KEY:
        abort(403, "Satellite API not configured")
    try:
        port = max(1, min(65535, int(request.args.get("port", "8080"))))
    except ValueError:
        abort(400, "Invalid port")
    try:
        capacity = max(1, int(request.args.get("capacity", "200")))
    except ValueError:
        abort(400, "Invalid capacity")
    install_dir = shorten(request.args.get("install_dir", SATELLITE_BOOTSTRAP_INSTALL_DIR), 240)
    name = shorten(request.args.get("name", ""), 120)
    origin_url = require_http_url(
        request.args.get("origin_url", "") or request_external_base_url(),
        "origin_url",
        allow_path=False,
    )
    raw_public_url = shorten(request.args.get("public_url", ""), 500) or ""
    if not raw_public_url:
        abort(400, "Missing public_url")
    public_url = normalize_satellite_public_url(raw_public_url)
    nginx_template = bootstrap_file_text("nginx.host.conf.template")

    return render_bootstrap_asset(
        "bootstrap.sh.template",
        {
            "__INSTALL_DIR_QUOTED__": shell_quote(install_dir),
            "__SATELLITE_NAME_QUOTED__": shell_quote(name),
            "__SATELLITE_PUBLIC_URL_QUOTED__": shell_quote(public_url),
            "__SATELLITE_MAX_VIEWERS_QUOTED__": shell_quote(str(capacity)),
            "__MAIN_SERVER_URL_QUOTED__": shell_quote(origin_url),
            "__SATELLITE_PORT_QUOTED__": shell_quote(str(port)),
            "__SATELLITE_API_KEY_QUOTED__": shell_quote(SATELLITE_API_KEY),
            "__AGENT_PY__": bootstrap_file_text("agent.py"),
            "__REQUIREMENTS_TXT__": bootstrap_file_text("requirements.txt"),
            "__NGINX_CONF_TEMPLATE__": nginx_template,
        },
    )


def parse_hls_log_line(line: str) -> tuple[float, str, bool] | None:
    text = line.strip()
    if not text:
        return None
    parts = text.split("|", 4)
    if len(parts) != 5:
        return None
    ts_raw, viewer_cookie, _forwarded_for, _remote_addr, via_satellite = parts
    try:
        timestamp = float(ts_raw)
    except ValueError:
        return None
    viewer_id = str(viewer_cookie or "").strip()
    if not viewer_id or viewer_id == "-":
        return None
    return timestamp, viewer_id, via_satellite == "1"


def local_hls_viewer_count(now: float | None = None) -> int | None:
    if not HLS_ACCESS_LOG:
        return None
    log_path = Path(HLS_ACCESS_LOG)
    if not log_path.exists():
        return None
    current = time.time() if now is None else now
    cutoff = current - HLS_VIEWER_WINDOW
    unique_ips: set[str] = set()
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            file_size = handle.tell()
            offset = max(0, file_size - HLS_LOG_TAIL_BYTES)
            handle.seek(offset)
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    lines = data.splitlines()
    if offset > 0:
        lines = lines[1:]
    for line in lines:
        parsed = parse_hls_log_line(line)
        if not parsed:
            continue
        timestamp, viewer_id, via_satellite_proxy = parsed
        if timestamp < cutoff or via_satellite_proxy or not viewer_id:
            continue
        unique_ips.add(viewer_id)
    return len(unique_ips)


def local_nginx_connection_count() -> int | None:
    if not NGINX_STATUS_URL:
        return None
    try:
        with urlopen(NGINX_STATUS_URL, timeout=2) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    match = re.search(r"Active connections:\s*(\d+)", payload)
    if not match:
        return None
    return max(0, int(match.group(1)) - 1)


def local_stream_viewer_count(now: float | None = None) -> tuple[int, bool]:
    current = time.time() if now is None else now
    log_count = local_hls_viewer_count(current)
    if log_count is not None:
        return log_count, True
    connection_count = local_nginx_connection_count()
    if connection_count is not None:
        return connection_count, True
    return 0, False


def healthy_satellite_viewer_count(
    now: float | None = None,
    exclude_name: str | None = None,
) -> int:
    init_db()
    current = time.time() if now is None else now
    cutoff = current - SATELLITE_UNHEALTHY_SECONDS
    with connect_db() as conn:
        if exclude_name:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(viewer_count), 0) AS count
                FROM satellites
                WHERE last_heartbeat >= ? AND name != ?
                """,
                (cutoff, exclude_name),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(viewer_count), 0) AS count
                FROM satellites
                WHERE last_heartbeat >= ?
                """,
                (cutoff,),
            ).fetchone()
    return int(row["count"]) if row else 0


def total_viewer_count(
    local_count: int | None = None,
    local_observed: bool | None = None,
) -> int:
    if local_observed is None:
        computed_local_count, computed_local_observed = local_stream_viewer_count()
        local_count = computed_local_count
        local_observed = computed_local_observed
    satellite_count = healthy_satellite_viewer_count(
        exclude_name=LOCAL_SATELLITE_NAME if local_observed else None
    )
    if local_observed:
        return max(0, int(local_count or 0)) + satellite_count
    return satellite_count


def build_state_snapshot(
    local_count: int | None = None,
    local_observed: bool | None = None,
) -> dict[str, int | bool]:
    if local_observed is None:
        computed_local_count, computed_local_observed = local_stream_viewer_count()
        local_count = computed_local_count
        local_observed = computed_local_observed
    local = max(0, int(local_count or 0)) if local_observed else 0
    return {
        "live": is_live(),
        "audio_live": is_audio_live(),
        "count": total_viewer_count(local_count=local, local_observed=bool(local_observed)),
        "local_count": local,
    }


def current_state_snapshot() -> dict[str, int | bool]:
    with state_lock:
        return dict(state_snapshot)


def refresh_state_snapshot() -> None:
    snapshot = build_state_snapshot()
    with state_lock:
        state_snapshot.update(snapshot)


def state_refresher() -> None:
    while True:
        refresh_state_snapshot()
        time.sleep(STATE_CACHE_SECONDS)


def ensure_state_task() -> None:
    global state_task_started
    if state_task_started:
        return
    init_db()
    refresh_state_snapshot()
    with state_lock:
        if state_task_started:
            return
        state_task_started = True
    Thread(target=state_refresher, daemon=True).start()


def record_stats() -> None:
    init_db()
    while True:
        count = total_viewer_count()
        now = int(time.time())
        ts = (now // STATS_SAMPLE_SECONDS) * STATS_SAMPLE_SECONDS
        cutoff = ts - 60 * 60 * 24
        with connect_db() as conn:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO viewer_stats(ts, count) VALUES(?, ?)",
                    (ts, count),
                )
                conn.execute("DELETE FROM viewer_stats WHERE ts < ?", (cutoff,))
        time.sleep(STATS_SAMPLE_SECONDS)


def prune_stale_satellites() -> None:
    init_db()
    while True:
        cutoff = time.time() - SATELLITE_PRUNE_SECONDS
        with connect_db() as conn:
            with conn:
                conn.execute("DELETE FROM satellites WHERE last_heartbeat < ?", (cutoff,))
        time.sleep(15)


def ensure_maintenance_tasks() -> None:
    global maintenance_task_started
    if maintenance_task_started:
        return
    init_db()
    with state_lock:
        if maintenance_task_started:
            return
        maintenance_task_started = True
    Thread(target=record_stats, daemon=True).start()
    Thread(target=prune_stale_satellites, daemon=True).start()


@app.get("/status")
def status():
    ensure_state_task()
    ensure_maintenance_tasks()
    return jsonify(current_state_snapshot())


@app.get("/audio-status")
def audio_status():
    ensure_state_task()
    return jsonify({"live": current_state_snapshot()["audio_live"]})


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
    init_db()
    minutes = request.args.get("minutes", "60")
    try:
        minutes_int = max(1, min(240, int(minutes)))
    except ValueError:
        minutes_int = 60
    cutoff = int(time.time()) - minutes_int * 60
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT ts, count FROM viewer_stats WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    return jsonify(
        {
            "points": [{"ts": int(row["ts"]), "count": int(row["count"])} for row in rows],
            "minutes": minutes_int,
        }
    )


def validate_satellite_api_key():
    if not SATELLITE_API_KEY:
        abort(403, "Satellite API not configured")
    data = request.get_json(silent=True) or {}
    key = data.get("api_key", "")
    if not isinstance(key, str) or not secrets.compare_digest(key, SATELLITE_API_KEY):
        abort(403, "Invalid API key")
    return data


def satellite_score(row: sqlite3.Row) -> float:
    now = time.time()
    last_heartbeat = float(row["last_heartbeat"])
    if now - last_heartbeat > SATELLITE_UNHEALTHY_SECONDS:
        return -1
    capacity = int(row["capacity_max_viewers"])
    viewers = int(row["viewer_count"])
    cpu = float(row["cpu_percent"])
    headroom = max(0, capacity - viewers)
    return headroom * (1 - min(cpu, 100.0) / 100.0)


def satellite_info(row: sqlite3.Row, now: float | None = None) -> dict[str, str | int | float | bool]:
    current = time.time() if now is None else now
    last_heartbeat = float(row["last_heartbeat"])
    age = round(current - last_heartbeat, 1)
    healthy = age <= SATELLITE_UNHEALTHY_SECONDS
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "viewer_count": int(row["viewer_count"]),
        "cpu_percent": float(row["cpu_percent"]),
        "bandwidth_mbps": float(row["bandwidth_mbps"]),
        "capacity_max_viewers": int(row["capacity_max_viewers"]),
        "last_heartbeat_age": age,
        "healthy": healthy,
    }


@app.post("/api/satellite/register")
def satellite_register():
    init_db()
    data = validate_satellite_api_key()
    name = shorten(data.get("name", ""), 120)
    url = shorten(data.get("url", ""), 500)
    if not url:
        abort(400, "Missing satellite url")
    sat_id = str(uuid.uuid4())
    now = time.time()
    with connect_db() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO satellites(
                    id, name, url, cpu_percent, bandwidth_mbps,
                    viewer_count, capacity_max_viewers, last_heartbeat, registered_at
                ) VALUES(?, ?, ?, 0, 0, 0, ?, ?, ?)
                """,
                (sat_id, name, url, int(data.get("capacity_max_viewers", 100)), now, now),
            )
    ensure_maintenance_tasks()
    app.logger.info("Satellite registered: %s (%s) at %s", sat_id, name, url)
    return jsonify(
        {
            "id": sat_id,
            "heartbeat_interval": SATELLITE_HEARTBEAT_INTERVAL,
        }
    )


@app.post("/api/satellite/<sat_id>/heartbeat")
def satellite_heartbeat(sat_id):
    init_db()
    data = validate_satellite_api_key()
    updated_at = time.time()
    with connect_db() as conn:
        with conn:
            cur = conn.execute(
                """
                UPDATE satellites
                SET cpu_percent = ?, bandwidth_mbps = ?, viewer_count = ?,
                    capacity_max_viewers = ?, last_heartbeat = ?
                WHERE id = ?
                """,
                (
                    min(100.0, max(0.0, float(data.get("cpu_percent", 0)))),
                    max(0.0, float(data.get("bandwidth_mbps", 0))),
                    max(0, int(data.get("viewer_count", 0))),
                    max(1, int(data.get("capacity_max_viewers", 100))),
                    updated_at,
                    sat_id,
                ),
            )
        if cur.rowcount == 0:
            abort(404, "Unknown satellite")
    return jsonify({"ok": True, "stream_active": is_live()})


@app.delete("/api/satellite/<sat_id>")
def satellite_deregister(sat_id):
    init_db()
    validate_satellite_api_key()
    removed = 0
    with connect_db() as conn:
        with conn:
            cur = conn.execute("DELETE FROM satellites WHERE id = ?", (sat_id,))
            removed = cur.rowcount
    if removed:
        app.logger.info("Satellite deregistered: %s", sat_id)
    return jsonify({"ok": True})


@app.get("/api/satellite/assign")
def satellite_assign():
    init_db()
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM satellites").fetchall()
    best_row = None
    best_score = -1.0
    for row in rows:
        score = satellite_score(row)
        if score > best_score:
            best_score = score
            best_row = row
    if best_row and best_score > 0:
        return jsonify({"satellite_url": best_row["url"]})
    return jsonify({"satellite_url": None})


@app.get("/api/satellites")
def satellite_list():
    init_db()
    now = time.time()
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM satellites ORDER BY name ASC, id ASC").fetchall()
    return jsonify({"satellites": [satellite_info(row, now) for row in rows]})


@app.get("/api/satellite/bootstrap.sh")
def satellite_bootstrap_script():
    return Response(
        render_satellite_bootstrap_script(),
        mimetype="text/x-shellscript",
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    init_db()
    ensure_state_task()
    ensure_maintenance_tasks()
    app.run(host="0.0.0.0", port=5000, debug=APP_DEBUG, use_reloader=False, threaded=True)
