import ipaddress
import base64
import http.client
import json
import os
import random
import secrets
import re
import socket
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from flask import Flask, Response, abort, jsonify, make_response, redirect, render_template, request, session, url_for
from scaleway import ScalewayAPIError, ScalewayConfig, ScalewayManager
try:
    import dns.exception
    import dns.resolver
except ImportError:  # pragma: no cover - fallback until dependencies are installed
    dns = None

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
HLS_INTERNAL_VIEWER_PREFIX = "__internal__:"
HLS_STATUS_PROBE_VIEWER_ID = f"{HLS_INTERNAL_VIEWER_PREFIX}status-probe"
HLS_VIEWER_COOKIE_DOMAIN = os.getenv("HLS_VIEWER_COOKIE_DOMAIN", "").strip().lstrip(".")
AUDIO_STREAM_NAME = os.getenv("AUDIO_STREAM_NAME", "").strip()
AUDIO_HLS_URL = os.getenv("AUDIO_HLS_URL", "").strip()
AUDIO_ONLY = parse_bool(os.getenv("AUDIO_ONLY", ""))
# The site uses a single theme defined entirely in static/css/theme.css. There
# is no theme selection — to change the look, edit the variables in that file.
SITE_TITLE = get_env_default("SITE_TITLE", "docker streaming")
SITE_SUBTITLE = get_env_default("SITE_SUBTITLE", "")
PAGE_TITLE = get_env_default("PAGE_TITLE", f"Live Stream - {SITE_TITLE}")
LOGO_URL = get_env_default("LOGO_URL", "")
LOGO_ALT = get_env_default("LOGO_ALT", "Your Logo Here")
FAVICON_URL = os.getenv("FAVICON_URL", "").strip()
FAVICON_TYPE = get_env_default("FAVICON_TYPE", "image/svg+xml")
# Open Graph preview image for link unfurls (Telegram, WhatsApp, etc.). Must be a
# raster format (PNG/JPEG) — SVG is not rendered by these services.
OG_IMAGE_URL = os.getenv("OG_IMAGE_URL", "").strip()
FOOTER_URL = get_env_default("FOOTER_URL", "")
FOOTER_TEXT = get_env_default("FOOTER_TEXT", "Your Footers Here")
FOOTER_TAGLINE = get_env_default("FOOTER_TAGLINE", "Live-Stream")
SCHEDULE_BASE_URL = get_env_default("SCHEDULE_BASE_URL", "")
# Selects which schedule-<name>.json file in SCHEDULE_BASE_URL is loaded. The
# frontend reads this from the <html data-theme> attribute (app.js).
SCHEDULE_NAME = get_env_default("SCHEDULE_NAME", "stephanus")
# Local filesystem path of that same schedule file, used by the admin editor to
# read and write entries. Defaults to the nginx-served data directory.
SCHEDULE_FILE = get_env_default(
    "SCHEDULE_FILE", f"/var/www/data/schedule-{SCHEDULE_NAME}.json"
)
STREAMING_HOST = os.getenv("STREAMING_HOST", "").strip()
# External RTMP ingest port and application name (see nginx.conf `application`),
# used to show the upload/ingest link in the admin area.
RTMP_PORT = get_env_default("RTMP_PORT", "1935")
RTMP_APP = get_env_default("RTMP_APP", "stream")
SATELLITE_API_KEY = os.getenv("SATELLITE_API_KEY", "").strip()
SATELLITE_BOOTSTRAP_TOKEN = os.getenv("SATELLITE_BOOTSTRAP_TOKEN", "").strip()
ADMIN_TOKEN = (
    os.getenv("ADMIN_TOKEN", "").strip()
    or os.getenv("SCW_MANAGE_TOKEN", "").strip()
)
SATELLITE_BOOTSTRAP_ORIGIN_URL = (
    os.getenv("SATELLITE_BOOTSTRAP_ORIGIN_URL", "").strip()
    or os.getenv("PUBLIC_BASE_URL", "").strip()
    or os.getenv("MAIN_SERVER_URL", "").strip()
)
SATELLITE_HEARTBEAT_INTERVAL = int(os.getenv("SATELLITE_HEARTBEAT_INTERVAL", "10"))
SATELLITE_UNHEALTHY_SECONDS = int(os.getenv("SATELLITE_UNHEALTHY_SECONDS", "30"))
SATELLITE_PRUNE_SECONDS = int(os.getenv("SATELLITE_PRUNE_SECONDS", "120"))
LOCAL_SATELLITE_NAME = get_env_default("LOCAL_SATELLITE_NAME", "main")
DNS_CHECK_CACHE_SECONDS = max(10.0, float(os.getenv("DNS_CHECK_CACHE_SECONDS", "60")))
DNS_CHECK_TIMEOUT_SECONDS = max(1.0, float(os.getenv("DNS_CHECK_TIMEOUT_SECONDS", "3")))
DNS_CHECK_NAMESERVERS = [
    entry.strip()
    for entry in os.getenv("DNS_CHECK_NAMESERVERS", "1.1.1.1,8.8.8.8,9.9.9.9").split(",")
    if entry.strip()
]
DNS_CHECK_QUORUM = max(1, min(len(DNS_CHECK_NAMESERVERS), int(os.getenv("DNS_CHECK_QUORUM", "2"))))
SATELLITE_BOOTSTRAP_INSTALL_DIR = (
    os.getenv("SATELLITE_BOOTSTRAP_INSTALL_DIR", "/opt/streaming-satellite").strip()
    or "/opt/streaming-satellite"
)
SATELLITE_BOOTSTRAP_NAME_PREFIX = get_env_default("SATELLITE_BOOTSTRAP_NAME_PREFIX", "node")
SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE = os.getenv(
    "SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE", ""
).strip()
SATELLITE_BOOTSTRAP_DIR = Path(
    os.getenv("SATELLITE_BOOTSTRAP_DIR", "/bootstrap/satellite").strip() or "/bootstrap/satellite"
)
SATELLITE_NODE_MAP_PATH = Path(
    os.getenv("SATELLITE_NODE_MAP_PATH", "").strip()
    or str(Path(STATE_DB).with_name("satellite-nodes.json"))
)
SATELLITE_CERT_CACHE_DIR = Path(
    os.getenv("SATELLITE_CERT_CACHE_DIR", "").strip()
    or str(Path(STATE_DB).with_name("satellite-certs"))
)
SATELLITE_CERT_CACHE_MAX_FILE_BYTES = 1024 * 1024
SATELLITE_CERT_CACHE_MAX_TOTAL_BYTES = 5 * 1024 * 1024
SATELLITE_PROBE_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("SATELLITE_PROBE_TIMEOUT_SECONDS", "5")),
)
SATELLITE_PROBE_CACHE_SECONDS = max(
    5.0,
    float(os.getenv("SATELLITE_PROBE_CACHE_SECONDS", "15")),
)
SATELLITE_ASSIGN_TIMEOUT_SECONDS = max(
    0.5,
    float(os.getenv("SATELLITE_ASSIGN_TIMEOUT_SECONDS", "2")),
)
APP_DEBUG = parse_bool(os.getenv("APP_DEBUG", "0"))
PUBLIC_ORIGIN_URL = (
    os.getenv("PUBLIC_BASE_URL", "").strip()
    or SATELLITE_BOOTSTRAP_ORIGIN_URL
    or (f"https://{STREAMING_HOST}" if STREAMING_HOST else "")
).rstrip("/")

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
dns_cache_lock = Lock()
dns_cache: dict[str, tuple[float, dict[str, object]]] = {}
probe_cache_lock = Lock()
probe_cache: dict[str, tuple[float, dict[str, object]]] = {}
node_map_lock = Lock()


class FixedAddressHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, connect_host: str, *args, **kwargs):
        self._connect_host = connect_host
        super().__init__(host, *args, **kwargs)

    def connect(self):
        self.sock = socket.create_connection(
            (self._connect_host, self.port),
            self.timeout,
            self.source_address,
        )


class FixedAddressHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, connect_host: str, *args, **kwargs):
        self._connect_host = connect_host
        super().__init__(host, *args, **kwargs)

    def connect(self):
        sock = socket.create_connection(
            (self._connect_host, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


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
                    observed_ip TEXT NOT NULL DEFAULT '',
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
            scaleway.ensure_db(conn)
            satellite_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(satellites)").fetchall()
            }
            if "observed_ip" not in satellite_columns:
                conn.execute(
                    "ALTER TABLE satellites ADD COLUMN observed_ip TEXT NOT NULL DEFAULT ''"
                )
        db_ready = True

def render_page_context(debug_enabled: bool) -> dict[str, object]:
    audio_hls_url = None
    if AUDIO_HLS_URL:
        audio_hls_url = AUDIO_HLS_URL
    elif AUDIO_STREAM_NAME:
        audio_hls_url = f"/hls/{AUDIO_STREAM_NAME}.m3u8"
    favicon_url = FAVICON_URL or url_for("static", filename="favicon.svg")
    return {
        "hls_url": f"/hls/{STREAM_NAME}.m3u8",
        "audio_hls_url": audio_hls_url,
        "local_satellite_name": LOCAL_SATELLITE_NAME,
        "site_title": SITE_TITLE,
        "site_subtitle": SITE_SUBTITLE,
        "page_title": PAGE_TITLE,
        "logo_url": LOGO_URL,
        "logo_alt": LOGO_ALT,
        "favicon_url": favicon_url,
        "favicon_type": FAVICON_TYPE,
        "og_image_url": OG_IMAGE_URL,
        "og_url": request_external_base_url(),
        "audio_only": AUDIO_ONLY,
        "debug_enabled": debug_enabled,
        "footer_url": FOOTER_URL,
        "footer_text": FOOTER_TEXT,
        "footer_tagline": FOOTER_TAGLINE,
        "schedule_base_url": SCHEDULE_BASE_URL,
        "schedule_name": SCHEDULE_NAME,
        "show_schedule": bool(SCHEDULE_BASE_URL),
        "scaleway_enabled": scaleway.feature_enabled(),
        "scaleway_default_zone": scaleway.config.default_zone,
        "scaleway_default_type": scaleway.config.default_commercial_type,
        "scaleway_server_limit": scaleway.config.server_limit,
        "scaleway_allowed_zones": scaleway.config.allowed_zones,
        "scaleway_zone_options": scaleway.available_zones(),
        "scaleway_server_types": scaleway.available_server_types(),
    }


def render_index(debug_enabled: bool):
    response = make_response(
        render_template(
            "index.html",
            **render_page_context(debug_enabled),
        )
    )
    current_viewer_cookie = request.cookies.get(HLS_VIEWER_COOKIE, "").strip()
    should_set_viewer_cookie = (not current_viewer_cookie) or bool(HLS_VIEWER_COOKIE_DOMAIN)
    if should_set_viewer_cookie:
        response.set_cookie(
            HLS_VIEWER_COOKIE,
            current_viewer_cookie or secrets.token_urlsafe(18),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=not APP_DEBUG,
            samesite="Lax",
            path="/",
            domain=HLS_VIEWER_COOKIE_DOMAIN or None,
        )
    return response


def admin_authenticated() -> bool:
    return bool(session.get("admin_authenticated"))


def require_admin() -> None:
    if not ADMIN_TOKEN:
        abort(503, "Admin token is not configured")
    if not admin_authenticated():
        abort(403, "Admin token required")


def render_admin_login(login_error: str, status_code: int = 200):
    context = render_page_context(False)
    context["page_title"] = f"{context['page_title']} Admin Login"
    context["login_error"] = login_error
    return render_template("admin_login.html", **context), status_code


@app.get("/")
def index():
    return render_index(False)


@app.route("/admin", methods=["GET", "POST"])
def admin_index():
    if request.method == "POST":
        if not ADMIN_TOKEN:
            abort(503, "Admin token is not configured")
        submitted = request.form.get("token", "").strip()
        if submitted and secrets.compare_digest(submitted, ADMIN_TOKEN):
            session["admin_authenticated"] = True
            return redirect(url_for("admin_index"))
        return render_admin_login("Ungültiger Admin-Token.", 403)
    if not admin_authenticated():
        return render_admin_login("", 200)
    return render_admin()


@app.post("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_index"))


def render_admin():
    context = render_page_context(True)
    context["page_title"] = f"{context['page_title']} Admin"
    # Encoder config: Server = the app URL with the auth key as a query string,
    # Stream Key = the stream name ("live").
    rtmp_server = f"rtmp://{STREAMING_HOST}:{RTMP_PORT}/{RTMP_APP}" if STREAMING_HOST else ""
    context["rtmp_url"] = (
        f"{rtmp_server}?key={STREAM_KEY}" if rtmp_server and STREAM_KEY else rtmp_server
    )
    context["rtmp_stream_key"] = STREAM_NAME
    response = make_response(
        render_template(
            "admin.html",
            **context,
        )
    )
    current_viewer_cookie = request.cookies.get(HLS_VIEWER_COOKIE, "").strip()
    should_set_viewer_cookie = (not current_viewer_cookie) or bool(HLS_VIEWER_COOKIE_DOMAIN)
    if should_set_viewer_cookie:
        response.set_cookie(
            HLS_VIEWER_COOKIE,
            current_viewer_cookie or secrets.token_urlsafe(18),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=not APP_DEBUG,
            samesite="Lax",
            path="/",
            domain=HLS_VIEWER_COOKIE_DOMAIN or None,
        )
    return response
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
    candidate = normalize_ip_address(addr)
    if not candidate:
        return False
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


def normalize_ip_address(addr: str) -> str:
    candidate = str(addr or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("["):
        end = candidate.find("]")
        if end != -1:
            candidate = candidate[1:end]
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass
    if candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.split(":", 1)[0].strip()
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return ""


def observed_request_ip() -> str:
    candidates = []
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        candidates.extend(part.strip() for part in forwarded.split(","))
    if request.remote_addr:
        candidates.append(request.remote_addr)
    normalized = [normalize_ip_address(candidate) for candidate in candidates]
    normalized = [candidate for candidate in normalized if candidate]
    for candidate in normalized:
        if not is_private_addr(candidate):
            return candidate
    return normalized[0] if normalized else ""


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


scaleway = ScalewayManager(
    config=ScalewayConfig.from_env(
        streaming_host=STREAMING_HOST,
        public_origin_url=PUBLIC_ORIGIN_URL,
        satellite_api_key=SATELLITE_API_KEY,
        satellite_bootstrap_token=SATELLITE_BOOTSTRAP_TOKEN,
        state_db_path=STATE_DB,
    ),
    connect_db=connect_db,
    init_db=init_db,
    shorten=shorten,
    normalize_ip_address=normalize_ip_address,
    request_external_base_url=request_external_base_url,
)


def normalize_satellite_public_url(value: str) -> str:
    normalized = try_normalize_satellite_public_url(value)
    if normalized:
        return normalized
    abort(400, "public_url must point to the satellite /hls base")


def try_normalize_satellite_public_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    path = parsed.path.rstrip("/")
    if path.endswith("/hls"):
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    if path not in {"", "/"}:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/hls"


def satellite_bootstrap_public_url_template() -> str:
    if SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE:
        return SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE
    if HLS_VIEWER_COOKIE_DOMAIN:
        return f"https://{{name}}.{HLS_VIEWER_COOKIE_DOMAIN}/hls"
    host = STREAMING_HOST.strip().strip(".")
    if not host:
        return ""
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 3:
        zone = ".".join(parts[1:])
    elif len(parts) == 2:
        zone = host
    else:
        zone = ""
    return f"https://{{name}}.{zone}/hls" if zone else ""


def extract_satellite_node_number(name: str) -> int | None:
    prefix = SATELLITE_BOOTSTRAP_NAME_PREFIX
    if not prefix:
        return None
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", str(name or "").strip())
    if not match:
        return None
    try:
        number = int(match.group(1))
    except ValueError:
        return None
    return number if number > 0 else None


def bootstrap_public_url_for_name(name: str, number: int | None = None) -> str:
    template = satellite_bootstrap_public_url_template()
    if not template:
        abort(
            400,
            "Missing public_url and SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE is not configured",
        )
    try:
        rendered = template.format(name=name, number=number or "")
    except (IndexError, KeyError, ValueError):
        abort(500, "Invalid SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE")
    public_url = try_normalize_satellite_public_url(rendered)
    if public_url:
        return public_url
    abort(500, "SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE did not render a valid satellite URL")


def node_record_public_host(record: dict[str, object]) -> str:
    parsed = urlparse(str(record.get("public_url", "") or "").strip())
    return parsed.hostname or ""


def normalize_node_record(record: object) -> dict[str, object] | None:
    if not isinstance(record, dict):
        return None
    name = shorten(record.get("name", ""), 120) or ""
    observed_ip = normalize_ip_address(str(record.get("observed_ip", "") or ""))
    public_url = try_normalize_satellite_public_url(str(record.get("public_url", "") or ""))
    if not name and not observed_ip and not public_url:
        return None
    number = record.get("number")
    try:
        parsed_number = int(number)
    except (TypeError, ValueError):
        parsed_number = extract_satellite_node_number(name)
    else:
        if parsed_number <= 0:
            parsed_number = extract_satellite_node_number(name)
    assigned_at_raw = record.get("assigned_at", time.time())
    updated_at_raw = record.get("updated_at", assigned_at_raw)
    try:
        assigned_at = float(assigned_at_raw)
    except (TypeError, ValueError):
        assigned_at = time.time()
    try:
        updated_at = float(updated_at_raw)
    except (TypeError, ValueError):
        updated_at = assigned_at
    return {
        "name": name,
        "number": parsed_number,
        "public_url": public_url,
        "host": node_record_public_host({"public_url": public_url}),
        "observed_ip": observed_ip,
        "assigned_at": assigned_at,
        "updated_at": max(updated_at, assigned_at),
    }


def read_node_records_unlocked() -> list[dict[str, object]]:
    if not SATELLITE_NODE_MAP_PATH.exists():
        return []
    try:
        raw = json.loads(SATELLITE_NODE_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        app.logger.warning("Unable to read satellite node map %s: %s", SATELLITE_NODE_MAP_PATH, exc)
        return []
    if isinstance(raw, dict):
        entries = raw.get("nodes", [])
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []
    normalized: list[dict[str, object]] = []
    for entry in entries:
        record = normalize_node_record(entry)
        if record is not None:
            normalized.append(record)
    return normalized


def write_node_records_unlocked(records: list[dict[str, object]]) -> None:
    SATELLITE_NODE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": records,
        "updated_at": time.time(),
    }
    tmp_path = SATELLITE_NODE_MAP_PATH.with_suffix(f"{SATELLITE_NODE_MAP_PATH.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(SATELLITE_NODE_MAP_PATH)


def load_node_records() -> list[dict[str, object]]:
    with node_map_lock:
        return read_node_records_unlocked()


def node_name_for_ip(ip_addr: str) -> str:
    normalized_ip = normalize_ip_address(ip_addr)
    if not normalized_ip:
        return ""
    for record in load_node_records():
        observed_ip = normalize_ip_address(str(record.get("observed_ip", "") or ""))
        if observed_ip == normalized_ip:
            return str(record.get("name", "") or "").strip()
    return ""


def find_node_record(
    records: list[dict[str, object]],
    *,
    observed_ip: str = "",
    name: str = "",
    public_url: str = "",
) -> dict[str, object] | None:
    normalized_ip = normalize_ip_address(observed_ip)
    normalized_name = str(name or "").strip()
    normalized_url = try_normalize_satellite_public_url(public_url)
    normalized_host = urlparse(normalized_url).hostname or ""
    if normalized_ip:
        for record in records:
            if str(record.get("observed_ip", "") or "") == normalized_ip:
                return record
    for record in records:
        if normalized_name and str(record.get("name", "") or "").strip() == normalized_name:
            return record
    for record in records:
        if normalized_url and str(record.get("public_url", "") or "") == normalized_url:
            return record
    for record in records:
        if normalized_host and str(record.get("host", "") or "") == normalized_host:
            return record
    return None


def upsert_node_record(name: str, public_url: str, observed_ip: str) -> dict[str, object]:
    normalized_name = shorten(name, 120) or ""
    normalized_url = try_normalize_satellite_public_url(public_url)
    normalized_ip = normalize_ip_address(observed_ip)
    if not normalized_name and not normalized_url and not normalized_ip:
        return {}
    with node_map_lock:
        records = read_node_records_unlocked()
        existing = find_node_record(
            records,
            observed_ip=normalized_ip,
            name=normalized_name,
            public_url=normalized_url,
        )
        current = time.time()
        if existing is None:
            record = normalize_node_record(
                {
                    "name": normalized_name,
                    "public_url": normalized_url,
                    "observed_ip": normalized_ip,
                    "assigned_at": current,
                    "updated_at": current,
                }
            )
            if record is None:
                return {}
            records.append(record)
            write_node_records_unlocked(records)
            return dict(record)
        if normalized_name:
            existing["name"] = normalized_name
        if normalized_url:
            existing["public_url"] = normalized_url
            existing["host"] = node_record_public_host(existing)
        if normalized_ip:
            existing["observed_ip"] = normalized_ip
        if not existing.get("number"):
            existing["number"] = extract_satellite_node_number(str(existing.get("name", "") or ""))
        existing["updated_at"] = current
        write_node_records_unlocked(records)
        return dict(existing)


def assign_bootstrap_node(observed_ip: str) -> dict[str, object]:
    normalized_ip = normalize_ip_address(observed_ip)
    with node_map_lock:
        records = read_node_records_unlocked()
        current = time.time()
        highest = 0
        for record in records:
            number = record.get("number")
            if isinstance(number, int) and number > highest:
                highest = number
        existing = find_node_record(records, observed_ip=normalized_ip)
        if existing is not None:
            existing_number = existing.get("number")
            if not isinstance(existing_number, int) or existing_number <= 0:
                highest += 1
                existing_number = highest
                existing["number"] = existing_number
            existing_name = str(existing.get("name", "") or "").strip()
            if not existing_name:
                existing_name = f"{SATELLITE_BOOTSTRAP_NAME_PREFIX}{existing_number}"
                existing["name"] = existing_name
            existing_url = str(existing.get("public_url", "") or "").strip()
            if not existing_url:
                existing_url = bootstrap_public_url_for_name(existing_name, existing_number)
                existing["public_url"] = existing_url
                existing["host"] = node_record_public_host(existing)
            existing["updated_at"] = current
            write_node_records_unlocked(records)
            return dict(existing)
        next_number = highest + 1
        name = f"{SATELLITE_BOOTSTRAP_NAME_PREFIX}{next_number}"
        public_url = bootstrap_public_url_for_name(name, next_number)
        record = normalize_node_record(
            {
                "name": name,
                "number": next_number,
                "public_url": public_url,
                "observed_ip": normalized_ip,
                "assigned_at": current,
                "updated_at": current,
            }
        )
        if record is None:
            abort(500, "Unable to allocate satellite node")
        records.append(record)
        write_node_records_unlocked(records)
        return dict(record)


def bootstrap_satellite_identity(observed_ip: str) -> tuple[str, str]:
    normalized_ip = normalize_ip_address(observed_ip)
    if request.args.get("name", "").strip() or request.args.get("public_url", "").strip():
        abort(400, "Bootstrap identity is auto-assigned by the main server")
    assigned = assign_bootstrap_node(normalized_ip)
    return str(assigned.get("name", "") or ""), str(assigned.get("public_url", "") or "")


def expected_node_ip(name: str, url: str, observed_ip: str = "") -> str:
    record = find_node_record(
        load_node_records(),
        observed_ip=observed_ip,
        name=name,
        public_url=url,
    )
    if record is None:
        return ""
    return normalize_ip_address(str(record.get("observed_ip", "") or ""))


def bootstrap_upstream_target(origin_url: str) -> tuple[str, str]:
    parsed = urlparse((origin_url or "").strip())
    upstream_host = parsed.hostname or ""
    if not upstream_host:
        return origin_url, ""
    # Keep the origin hostname in the generated installer instead of pinning
    # whatever address the bootstrap server happens to resolve at render time.
    return origin_url, upstream_host


def satellite_health_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/health"


def satellite_manifest_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/hls"):
        return f"{parsed.scheme}://{parsed.netloc}{base_path}/{STREAM_NAME}.m3u8"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}/hls/{STREAM_NAME}.m3u8"


def http_probe_once(
    url: str,
    headers: dict[str, str] | None = None,
    connect_ip: str = "",
    method: str = "GET",
) -> dict[str, object]:
    parsed = urlparse(url)
    request_headers = {"User-Agent": "streaming-status/1.0"}
    if headers:
        request_headers.update({key: value for key, value in headers.items() if value})
    host = parsed.hostname or ""
    if host and "Host" not in request_headers:
        request_headers["Host"] = parsed.netloc
    target = normalize_ip_address(connect_ip)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    connection = None
    try:
        if scheme == "https":
            if target:
                connection = FixedAddressHTTPSConnection(
                    host,
                    target,
                    port=port,
                    timeout=SATELLITE_PROBE_TIMEOUT_SECONDS,
                )
            else:
                connection = http.client.HTTPSConnection(
                    host,
                    port=port,
                    timeout=SATELLITE_PROBE_TIMEOUT_SECONDS,
                )
        elif scheme == "http":
            if target:
                connection = FixedAddressHTTPConnection(
                    host,
                    target,
                    port=port,
                    timeout=SATELLITE_PROBE_TIMEOUT_SECONDS,
                )
            else:
                connection = http.client.HTTPConnection(
                    host,
                    port=port,
                    timeout=SATELLITE_PROBE_TIMEOUT_SECONDS,
                )
        else:
            return {
                "status": 0,
                "headers": {},
                "body": "",
                "final_url": url,
                "error": "Unsupported scheme",
            }
        connection.request(method.upper(), path, headers=request_headers)
        response = connection.getresponse()
        body = response.read(256).decode("utf-8", errors="ignore")
        return {
            "status": int(getattr(response, "status", 200) or 200),
            "headers": dict(response.getheaders()),
            "body": body,
            "final_url": url,
            "error": "",
        }
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        return {
            "status": 0,
            "headers": {},
            "body": "",
            "final_url": url,
            "error": shorten(getattr(exc, "strerror", None) or str(exc), 120) or "",
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {
            "status": 0,
            "headers": {},
            "body": "",
            "final_url": url,
            "error": shorten(str(exc), 120) or "",
        }
    finally:
        if connection is not None:
            connection.close()


def probe_candidate_ips(url: str, observed_ip: str = "") -> list[str]:
    parsed = urlparse(str(url or "").strip())
    host = parsed.hostname or ""
    candidates: list[str] = []
    normalized_observed = normalize_ip_address(observed_ip)
    if normalized_observed and not is_private_addr(normalized_observed):
        candidates.append(normalized_observed)
    if not host:
        return candidates
    try:
        normalized_host = ipaddress.ip_address(host).compressed
    except ValueError:
        resolved = resolve_dns_addresses(host)
        for address in resolved.get("dns_addresses", []):
            normalized = normalize_ip_address(str(address))
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    else:
        if normalized_host not in candidates:
            candidates.append(normalized_host)
    return candidates


def http_probe(
    url: str,
    headers: dict[str, str] | None = None,
    observed_ip: str = "",
    method: str = "GET",
) -> dict[str, object]:
    last_result: dict[str, object] | None = None
    for candidate in probe_candidate_ips(url, observed_ip):
        result = http_probe_once(url, headers, candidate, method)
        last_result = result
        if int(result.get("status", 0) or 0) > 0:
            return result
    if last_result is not None:
        return last_result
    return http_probe_once(url, headers, method=method)


def is_internal_viewer_id(viewer_id: str) -> bool:
    normalized = str(viewer_id or "").strip()
    return bool(normalized) and normalized.startswith(HLS_INTERNAL_VIEWER_PREFIX)


def public_probe_status_for_url(value: str, observed_ip: str = "", now: float | None = None) -> dict[str, object]:
    current = time.time() if now is None else now
    normalized_value = str(value or "").strip()
    cache_key = f"{normalized_value}|{normalize_ip_address(observed_ip)}"
    if not normalized_value:
        return {
            "public_ok": False,
            "health_ok": False,
            "health_label": "No URL",
            "health_status": 0,
            "health_url": "",
            "hls_ok": False,
            "hls_label": "No URL",
            "hls_status": 0,
            "hls_preflight_status": 0,
            "hls_url": "",
            "hls_allow_origin": "",
            "hls_allow_credentials": "",
        }

    with probe_cache_lock:
        cached = probe_cache.get(cache_key)
    if cached and current - cached[0] <= SATELLITE_PROBE_CACHE_SECONDS:
        return dict(cached[1])

    health_url = satellite_health_url(normalized_value)
    hls_url = satellite_manifest_url(normalized_value)
    origin_headers = {"Origin": PUBLIC_ORIGIN_URL} if PUBLIC_ORIGIN_URL else {}
    viewer_headers = dict(origin_headers)
    viewer_headers["Cookie"] = f"{HLS_VIEWER_COOKIE}={HLS_STATUS_PROBE_VIEWER_ID}"
    preflight_headers = dict(origin_headers)
    preflight_headers["Access-Control-Request-Method"] = "GET"
    preflight_headers["Access-Control-Request-Headers"] = "Range"

    health_probe = (
        http_probe(health_url, origin_headers, observed_ip)
        if health_url else
        {"status": 0, "body": "", "error": "Missing URL"}
    )
    hls_probe = (
        http_probe(hls_url, viewer_headers, observed_ip)
        if hls_url else
        {"status": 0, "body": "", "error": "Missing URL"}
    )
    preflight_probe = (
        http_probe(hls_url, preflight_headers, observed_ip, method="OPTIONS")
        if hls_url and PUBLIC_ORIGIN_URL else
        {"status": 0, "headers": {}}
    )

    health_body = str(health_probe.get("body", "") or "").strip().lower()
    health_status = int(health_probe.get("status", 0) or 0)
    health_ok = health_status == 200 and (not health_body or health_body.startswith("ok"))
    health_label = "200" if health_ok else (
        str(health_status) if health_status else shorten(str(health_probe.get("error", "") or "Fail"), 24)
    )

    hls_status = int(hls_probe.get("status", 0) or 0)
    hls_headers = {str(key).lower(): str(value) for key, value in dict(hls_probe.get("headers", {})).items()}
    hls_body = str(hls_probe.get("body", "") or "")
    content_type = hls_headers.get("content-type", "").lower()
    allow_origin = hls_headers.get("access-control-allow-origin", "")
    allow_credentials = hls_headers.get("access-control-allow-credentials", "")
    preflight_status = int(preflight_probe.get("status", 0) or 0)
    preflight_response_headers = {
        str(key).lower(): str(value)
        for key, value in dict(preflight_probe.get("headers", {})).items()
    }
    preflight_allow_origin = preflight_response_headers.get("access-control-allow-origin", "")
    preflight_allow_credentials = preflight_response_headers.get("access-control-allow-credentials", "")
    preflight_allow_methods = {
        value.strip().upper()
        for value in preflight_response_headers.get("access-control-allow-methods", "").split(",")
    }
    preflight_allow_headers = {
        value.strip().lower()
        for value in preflight_response_headers.get("access-control-allow-headers", "").split(",")
    }
    manifest_ok = hls_status == 200 and (
        "#EXTM3U" in hls_body or "application/vnd.apple.mpegurl" in content_type
    )
    cors_ok = True
    if PUBLIC_ORIGIN_URL:
        cors_ok = (
            allow_origin == PUBLIC_ORIGIN_URL
            and allow_credentials.lower() == "true"
            and preflight_status in {200, 204}
            and preflight_allow_origin == PUBLIC_ORIGIN_URL
            and preflight_allow_credentials.lower() == "true"
            and "GET" in preflight_allow_methods
            and "range" in preflight_allow_headers
        )
    hls_ok = manifest_ok and cors_ok
    if hls_ok:
        hls_label = "200"
    elif manifest_ok and not cors_ok:
        hls_label = "CORS"
    elif hls_status:
        hls_label = str(hls_status)
    else:
        hls_label = shorten(str(hls_probe.get("error", "") or "Fail"), 24)

    resolved = {
        "public_ok": health_ok and hls_ok,
        "health_ok": health_ok,
        "health_label": health_label,
        "health_status": health_status,
        "health_url": health_url,
        "hls_ok": hls_ok,
        "hls_label": hls_label,
        "hls_status": hls_status,
        "hls_preflight_status": preflight_status,
        "hls_url": hls_url,
        "hls_allow_origin": allow_origin,
        "hls_allow_credentials": allow_credentials,
    }
    with probe_cache_lock:
        probe_cache[cache_key] = (current, resolved)
    return dict(resolved)


def local_probe_status_for_url(value: str) -> dict[str, object]:
    return {
        "public_ok": True,
        "health_ok": True,
        "health_label": "Local",
        "health_status": 200,
        "health_url": satellite_health_url(value),
        "hls_ok": True,
        "hls_label": "Same-origin",
        "hls_status": 200,
        "hls_preflight_status": 200,
        "hls_url": satellite_manifest_url(value),
        "hls_allow_origin": "",
        "hls_allow_credentials": "",
    }


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
    observed_ip = observed_request_ip()
    default_origin_url = SATELLITE_BOOTSTRAP_ORIGIN_URL or request_external_base_url()
    origin_url = require_http_url(
        request.args.get("origin_url", "") or default_origin_url,
        "origin_url",
        allow_path=False,
    )
    name, public_url = bootstrap_satellite_identity(observed_ip)
    origin_upstream_url, origin_upstream_host = bootstrap_upstream_target(origin_url)
    nginx_template = bootstrap_file_text("nginx.host.conf.template")

    return render_bootstrap_asset(
        "bootstrap.sh.template",
        {
            "__INSTALL_DIR_QUOTED__": shell_quote(install_dir),
            "__SATELLITE_NAME_QUOTED__": shell_quote(name),
            "__SATELLITE_PUBLIC_URL_QUOTED__": shell_quote(public_url),
            "__SATELLITE_MAX_VIEWERS_QUOTED__": shell_quote(str(capacity)),
            "__MAIN_SERVER_URL_QUOTED__": shell_quote(origin_url),
            "__MAIN_SERVER_UPSTREAM_URL_QUOTED__": shell_quote(origin_upstream_url),
            "__MAIN_SERVER_UPSTREAM_HOST_QUOTED__": shell_quote(origin_upstream_host),
            "__SATELLITE_PORT_QUOTED__": shell_quote(str(port)),
            "__SATELLITE_API_KEY_QUOTED__": shell_quote(SATELLITE_API_KEY),
            "__AGENT_PY__": bootstrap_file_text("agent.py"),
            "__REQUIREMENTS_TXT__": bootstrap_file_text("requirements.txt"),
            "__NGINX_CONF_TEMPLATE__": nginx_template,
            "__CADDYFILE_TEMPLATE__": bootstrap_file_text("Caddyfile.template"),
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
    if not viewer_id or viewer_id == "-" or is_internal_viewer_id(viewer_id):
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


def local_satellite_viewer_count(now: float | None = None) -> tuple[int, bool]:
    init_db()
    current = time.time() if now is None else now
    cutoff = current - SATELLITE_UNHEALTHY_SECONDS
    with connect_db() as conn:
        row = conn.execute(
            """
            SELECT viewer_count
            FROM satellites
            WHERE name = ? AND last_heartbeat >= ?
            ORDER BY last_heartbeat DESC, id DESC
            LIMIT 1
            """,
            (LOCAL_SATELLITE_NAME, cutoff),
        ).fetchone()
    if row is None:
        return 0, False
    return max(0, int(row["viewer_count"])), True


def effective_local_viewer_count(now: float | None = None) -> tuple[int, bool]:
    local_satellite_count, local_satellite_observed = local_satellite_viewer_count(now)
    if local_satellite_observed:
        return local_satellite_count, True
    return local_stream_viewer_count(now)


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
        computed_local_count, computed_local_observed = effective_local_viewer_count()
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
        computed_local_count, computed_local_observed = effective_local_viewer_count()
        local_count = computed_local_count
        local_observed = computed_local_observed
    local = max(0, int(local_count or 0)) if local_observed else 0
    cluster = total_viewer_count(local_count=local, local_observed=bool(local_observed))
    return {
        "live": is_live(),
        "audio_live": is_audio_live(),
        "count": cluster,
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
    require_admin()
    init_db()
    minutes = request.args.get("minutes", "60")
    bucket_minutes = request.args.get("bucket_minutes", "1")
    try:
        minutes_int = max(1, min(240, int(minutes)))
    except ValueError:
        minutes_int = 60
    try:
        bucket_minutes_int = max(1, min(60, int(bucket_minutes)))
    except ValueError:
        bucket_minutes_int = 1
    # Größere Buckets als der gewählte Zeitraum sind nicht sinnvoll.
    bucket_minutes_int = min(bucket_minutes_int, minutes_int)
    cutoff = int(time.time()) - minutes_int * 60
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT ts, count FROM viewer_stats WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    bucket_seconds = bucket_minutes_int * 60
    aggregated_points: dict[int, tuple[int, int]] = {}
    for row in rows:
        ts = int(row["ts"])
        count = int(row["count"])
        bucket_ts = ts - (ts % bucket_seconds)
        sum_count, sample_count = aggregated_points.get(bucket_ts, (0, 0))
        aggregated_points[bucket_ts] = (sum_count + count, sample_count + 1)
    points = [
        {
            "ts": ts,
            "count": max(0, round(sum_count / sample_count)) if sample_count > 0 else 0,
        }
        for ts, (sum_count, sample_count) in sorted(aggregated_points.items())
    ]
    return jsonify(
        {
            "points": points,
            "minutes": minutes_int,
            "bucket_minutes": bucket_minutes_int,
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


def satellite_cert_cache_host(data: dict[str, object]) -> str:
    host = shorten(str(data.get("host", "") or "").strip().lower(), 253)
    if not host:
        public_url = str(data.get("url", "") or data.get("public_url", "") or "").strip()
        host = (urlparse(public_url).hostname or "").strip().lower()
    if not host:
        abort(400, "Missing certificate host")
    if len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        abort(400, "Invalid certificate host")
    if host.startswith(".") or host.endswith(".") or ".." in host:
        abort(400, "Invalid certificate host")
    return host


def satellite_cert_cache_path(host: str) -> Path:
    return SATELLITE_CERT_CACHE_DIR / f"{host}.json"


def validate_cert_cache_relpath(value: object) -> str:
    relpath = str(value or "").strip()
    if not relpath or relpath.startswith("/") or "\\" in relpath:
        abort(400, "Invalid certificate file path")
    parts = Path(relpath).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        abort(400, "Invalid certificate file path")
    if len(relpath) > 500:
        abort(400, "Certificate file path too long")
    return relpath


def validate_cert_cache_files(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        abort(400, "Missing certificate files")
    files: dict[str, str] = {}
    total = 0
    for raw_path, raw_content in value.items():
        relpath = validate_cert_cache_relpath(raw_path)
        if not isinstance(raw_content, str) or not raw_content:
            abort(400, "Invalid certificate file content")
        try:
            decoded = base64.b64decode(raw_content.encode("ascii"), validate=True)
        except Exception:
            abort(400, "Invalid certificate file encoding")
        size = len(decoded)
        if size <= 0 or size > SATELLITE_CERT_CACHE_MAX_FILE_BYTES:
            abort(400, "Certificate file too large")
        total += size
        if total > SATELLITE_CERT_CACHE_MAX_TOTAL_BYTES:
            abort(400, "Certificate cache upload too large")
        files[relpath] = raw_content
    return files


def write_satellite_cert_cache(host: str, payload: dict[str, object]) -> None:
    SATELLITE_CERT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = satellite_cert_cache_path(host)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def read_satellite_cert_cache(host: str) -> dict[str, object] | None:
    path = satellite_cert_cache_path(host)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        app.logger.warning("Unable to read satellite certificate cache %s: %s", path, exc)
        return None
    if not isinstance(raw, dict) or raw.get("host") != host or not isinstance(raw.get("files"), dict):
        return None
    return raw


def satellite_score(row: sqlite3.Row) -> float:
    now = time.time()
    last_heartbeat = float(row["last_heartbeat"])
    if now - last_heartbeat > SATELLITE_UNHEALTHY_SECONDS:
        return -1
    capacity = int(row["capacity_max_viewers"])
    viewers = int(row["viewer_count"])
    cpu = float(row["cpu_percent"])
    cpu_factor = 1 - min(cpu, 100.0) / 100.0
    headroom = capacity - viewers
    if headroom > 0:
        # Normal case: weight by spare capacity (unchanged behaviour).
        return headroom * cpu_factor
    # At/over capacity: never hard-cap. Keep every healthy node eligible with a
    # small, strictly-positive overflow weight so viewers keep spreading across
    # the existing nodes instead of falling back to the main server. The weight
    # stays < 1 (so any node with real headroom is always preferred), favours the
    # relatively least-loaded node (more capacity per current viewer), and shrinks
    # as a node goes further over capacity, so overflow distributes evenly.
    return (capacity / (viewers + 1)) * cpu_factor


def select_weighted_satellite(
    scored_rows: list[tuple[float, dict[str, object]]],
) -> dict[str, object] | None:
    candidates = [(score, row) for score, row in scored_rows if score > 0]
    if not candidates:
        return None
    selection = random.uniform(0, sum(score for score, _row in candidates))
    cumulative = 0.0
    for score, row in candidates:
        cumulative += score
        if selection <= cumulative:
            return row
    return candidates[-1][1]


def is_local_satellite(row: sqlite3.Row) -> bool:
    return str(row["name"] or "").strip() == LOCAL_SATELLITE_NAME


def satellite_heartbeat_healthy(row: sqlite3.Row, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return current - float(row["last_heartbeat"]) <= SATELLITE_UNHEALTHY_SECONDS


def resolve_dns_addresses(host: str) -> dict[str, object]:
    if dns is not None and DNS_CHECK_NAMESERVERS:
        vote_counts: dict[str, int] = {}
        errors: list[str] = []
        for nameserver in DNS_CHECK_NAMESERVERS:
            resolver = dns.resolver.Resolver(configure=False)
            resolver.nameservers = [nameserver]
            resolver.timeout = DNS_CHECK_TIMEOUT_SECONDS
            resolver.lifetime = DNS_CHECK_TIMEOUT_SECONDS
            try:
                for record_type in ("A", "AAAA"):
                    try:
                        answers = resolver.resolve(host, record_type)
                    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                        continue
                    seen_for_resolver: set[str] = set()
                    for answer in answers:
                        normalized = normalize_ip_address(getattr(answer, "address", str(answer)))
                        if normalized and normalized not in seen_for_resolver:
                            seen_for_resolver.add(normalized)
                    for normalized in seen_for_resolver:
                        vote_counts[normalized] = vote_counts.get(normalized, 0) + 1
            except dns.exception.DNSException as exc:
                errors.append(f"{nameserver}:{shorten(str(exc), 40)}")
                continue

        addresses = sorted(
            address
            for address, count in vote_counts.items()
            if count >= DNS_CHECK_QUORUM
        )
        if addresses:
            return {
                "dns_host": host,
                "dns_addresses": addresses,
                "dns_error": "",
                "dns_source": f"public:{','.join(DNS_CHECK_NAMESERVERS)} quorum={DNS_CHECK_QUORUM}",
            }

        if vote_counts:
            return {
                "dns_host": host,
                "dns_addresses": sorted(vote_counts),
                "dns_error": f"No quorum ({DNS_CHECK_QUORUM}/{len(DNS_CHECK_NAMESERVERS)})",
                "dns_source": f"public:{','.join(DNS_CHECK_NAMESERVERS)} quorum={DNS_CHECK_QUORUM}",
            }

        if errors:
            return {
                "dns_host": host,
                "dns_addresses": [],
                "dns_error": shorten("; ".join(errors), 80),
                "dns_source": f"public:{','.join(DNS_CHECK_NAMESERVERS)} quorum={DNS_CHECK_QUORUM}",
            }

        return {
            "dns_host": host,
            "dns_addresses": [],
            "dns_error": "",
            "dns_source": f"public:{','.join(DNS_CHECK_NAMESERVERS)} quorum={DNS_CHECK_QUORUM}",
        }

    try:
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addresses = sorted({normalize_ip_address(item[4][0]) for item in results if item[4]})
        return {
            "dns_host": host,
            "dns_addresses": [address for address in addresses if address],
            "dns_error": "",
            "dns_source": "system-resolver",
        }
    except OSError as exc:
        return {
            "dns_host": host,
            "dns_addresses": [],
            "dns_error": shorten(getattr(exc, "strerror", None) or str(exc), 80),
            "dns_source": "system-resolver",
        }


def dns_status_for_url(
    url: str,
    observed_ip: str = "",
    now: float | None = None,
    expected_ip: str = "",
) -> dict[str, object]:
    current = time.time() if now is None else now
    host = urlparse(str(url or "").strip()).hostname or ""
    normalized_observed_ip = normalize_ip_address(observed_ip)
    normalized_expected_ip = normalize_ip_address(expected_ip)
    comparison_ip = normalized_expected_ip or normalized_observed_ip
    comparison_public = bool(comparison_ip) and not is_private_addr(comparison_ip)
    comparison_source = "node-map" if normalized_expected_ip else "observed"
    if not host:
        return {
            "dns_ok": False,
            "dns_label": "Invalid URL",
            "dns_host": "",
            "dns_addresses": [],
            "dns_matches_observed": False,
            "observed_ip": normalized_observed_ip,
            "expected_ip": normalized_expected_ip,
            "dns_source": "",
        }
    try:
        normalized_host_ip = ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    else:
        matches_observed = (not comparison_public) or normalized_host_ip == comparison_ip
        return {
            "dns_ok": matches_observed,
            "dns_label": (
                "Direct IP" if not comparison_public else ("Match" if matches_observed else "Mismatch")
            ),
            "dns_host": host,
            "dns_addresses": [normalized_host_ip],
            "dns_matches_observed": matches_observed,
            "observed_ip": normalized_observed_ip,
            "expected_ip": normalized_expected_ip,
            "dns_source": "direct-ip" if not comparison_public else f"direct-ip ({comparison_source})",
        }

    resolved: dict[str, object] | None = None
    with dns_cache_lock:
        cached = dns_cache.get(host)
        if cached and current - cached[0] <= DNS_CHECK_CACHE_SECONDS:
            resolved = dict(cached[1])

    if resolved is None:
        resolved = resolve_dns_addresses(host)
        with dns_cache_lock:
            dns_cache[host] = (current, resolved)

    addresses = list(resolved.get("dns_addresses", []))
    dns_error = str(resolved.get("dns_error", "") or "")
    dns_source = str(resolved.get("dns_source", "") or "")
    if dns_error:
        return {
            "dns_ok": False,
            "dns_label": dns_error,
            "dns_host": host,
            "dns_addresses": addresses,
            "dns_matches_observed": False,
            "observed_ip": normalized_observed_ip,
            "expected_ip": normalized_expected_ip,
            "dns_source": dns_source if not comparison_ip else f"{dns_source} ({comparison_source})",
        }
    if not addresses:
        return {
            "dns_ok": False,
            "dns_label": "No records",
            "dns_host": host,
            "dns_addresses": [],
            "dns_matches_observed": False,
            "observed_ip": normalized_observed_ip,
            "expected_ip": normalized_expected_ip,
            "dns_source": dns_source if not comparison_ip else f"{dns_source} ({comparison_source})",
        }

    matches_observed = (not comparison_public) or comparison_ip in addresses
    data = {
        "dns_ok": matches_observed,
        "dns_label": (
            "Resolved" if not comparison_public else ("Match" if matches_observed else "Mismatch")
        ),
        "dns_host": host,
        "dns_addresses": addresses,
        "dns_matches_observed": matches_observed,
        "observed_ip": normalized_observed_ip,
        "expected_ip": normalized_expected_ip,
        "dns_source": dns_source if not comparison_ip else f"{dns_source} ({comparison_source})",
    }
    return data


def satellite_info(row: sqlite3.Row, now: float | None = None) -> dict[str, str | int | float | bool]:
    current = time.time() if now is None else now
    last_heartbeat = float(row["last_heartbeat"])
    age = round(current - last_heartbeat, 1)
    heartbeat_healthy = satellite_heartbeat_healthy(row, current)
    local_satellite = is_local_satellite(row)
    expected_ip = expected_node_ip(
        str(row["name"] or ""),
        str(row["url"] or ""),
        str(row["observed_ip"] or ""),
    )
    info = {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "observed_ip": normalize_ip_address(str(row["observed_ip"] or "")),
        "expected_ip": expected_ip,
        "viewer_count": int(row["viewer_count"]),
        "cpu_percent": float(row["cpu_percent"]),
        "bandwidth_mbps": float(row["bandwidth_mbps"]),
        "capacity_max_viewers": int(row["capacity_max_viewers"]),
        "last_heartbeat_age": age,
        "heartbeat_healthy": heartbeat_healthy,
        "local": local_satellite,
    }
    info.update(
        dns_status_for_url(
            str(row["url"] or ""),
            str(row["observed_ip"] or ""),
            current,
            expected_ip,
        )
    )
    if local_satellite:
        info.update(local_probe_status_for_url(str(row["url"] or "")))
        info["healthy"] = bool(info["heartbeat_healthy"]) and bool(info["dns_ok"])
    else:
        info.update(public_probe_status_for_url(str(row["url"] or ""), str(row["observed_ip"] or ""), current))
        info["healthy"] = bool(info["heartbeat_healthy"]) and bool(info["public_ok"])
    return info


def delete_replaced_satellite_rows(conn: sqlite3.Connection, name: str, url: str) -> int:
    cur = conn.execute(
        """
        DELETE FROM satellites
        WHERE url = ? OR (? != '' AND name = ?)
        """,
        (url, name, name),
    )
    return cur.rowcount


@app.post("/api/satellite/register")
def satellite_register():
    init_db()
    data = validate_satellite_api_key()
    name = shorten(data.get("name", ""), 120)
    url = shorten(data.get("url", ""), 500)
    observed_ip = observed_request_ip()
    if not url:
        abort(400, "Missing satellite url")
    sat_id = str(uuid.uuid4())
    now = time.time()
    upsert_node_record(name or "", url or "", observed_ip)
    with connect_db() as conn:
        with conn:
            replaced = delete_replaced_satellite_rows(conn, name or "", url)
            conn.execute(
                """
                INSERT INTO satellites(
                    id, name, url, observed_ip, cpu_percent, bandwidth_mbps,
                    viewer_count, capacity_max_viewers, last_heartbeat, registered_at
                ) VALUES(?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
                """,
                (
                    sat_id,
                    name,
                    url,
                    observed_ip,
                    int(data.get("capacity_max_viewers", 100)),
                    now,
                    now,
                ),
            )
    if replaced:
        app.logger.info(
            "Satellite registration replaced %d stale row(s): name=%s url=%s",
            replaced,
            name or "-",
            url,
        )
    ensure_maintenance_tasks()
    app.logger.info(
        "Satellite registered: %s (%s) at %s observed_ip=%s",
        sat_id,
        name,
        url,
        observed_ip or "-",
    )
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
    observed_ip = observed_request_ip()
    row = None
    with connect_db() as conn:
        with conn:
            cur = conn.execute(
                """
                UPDATE satellites
                SET observed_ip = ?, cpu_percent = ?, bandwidth_mbps = ?, viewer_count = ?,
                    capacity_max_viewers = ?, last_heartbeat = ?
                WHERE id = ?
                """,
                (
                    observed_ip,
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
        row = conn.execute(
            "SELECT name, url FROM satellites WHERE id = ?",
            (sat_id,),
        ).fetchone()
    if row is not None:
        upsert_node_record(str(row["name"] or ""), str(row["url"] or ""), observed_ip)
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


@app.post("/api/satellite/cert-cache/upload")
def satellite_cert_cache_upload():
    data = validate_satellite_api_key()
    host = satellite_cert_cache_host(data)
    files = validate_cert_cache_files(data.get("files"))
    payload = {
        "host": host,
        "name": shorten(str(data.get("name", "") or ""), 120),
        "public_url": shorten(str(data.get("public_url", "") or data.get("url", "") or ""), 500),
        "fingerprint": shorten(str(data.get("fingerprint", "") or ""), 128),
        "uploaded_at": time.time(),
        "files": files,
    }
    write_satellite_cert_cache(host, payload)
    app.logger.info(
        "Satellite certificate cache uploaded: host=%s files=%d name=%s",
        host,
        len(files),
        payload["name"] or "-",
    )
    return jsonify({"ok": True, "host": host, "files": len(files)})


@app.post("/api/satellite/cert-cache/download")
def satellite_cert_cache_download():
    data = validate_satellite_api_key()
    host = satellite_cert_cache_host(data)
    payload = read_satellite_cert_cache(host)
    if payload is None:
        return jsonify({"ok": True, "found": False, "host": host, "files": {}})
    return jsonify(
        {
            "ok": True,
            "found": True,
            "host": host,
            "uploaded_at": payload.get("uploaded_at"),
            "fingerprint": payload.get("fingerprint", ""),
            "files": payload.get("files", {}),
        }
    )


@app.get("/api/satellite/assign")
def satellite_assign():
    init_db()
    now = time.time()
    exclude_url = (request.args.get("exclude") or "").rstrip("/")
    with connect_db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM satellites").fetchall()]

    candidate_rows = [row for row in rows if satellite_score(row) > 0]
    if exclude_url:
        alternates = [row for row in candidate_rows if str(row["url"] or "").rstrip("/") != exclude_url]
        if alternates:
            candidate_rows = alternates

    def local_candidate(row: dict[str, object]) -> tuple[float, dict[str, object] | None]:
        if not satellite_heartbeat_healthy(row, now):
            return -1.0, None
        dns_status = dns_status_for_url(
            str(row["url"] or ""),
            str(row["observed_ip"] or ""),
            now,
            expected_node_ip(
                str(row.get("name", "") or ""),
                str(row.get("url", "") or ""),
                str(row.get("observed_ip", "") or ""),
            ),
        )
        if not bool(dns_status["dns_ok"]):
            return -1.0, None
        return satellite_score(row), row

    def remote_candidate(row: dict[str, object]) -> tuple[float, dict[str, object] | None]:
        if not satellite_heartbeat_healthy(row, now):
            return -1.0, None
        probe_status = public_probe_status_for_url(str(row["url"] or ""), str(row["observed_ip"] or ""), now)
        if not bool(probe_status["public_ok"]):
            return -1.0, None
        return satellite_score(row), row

    best_local_row = None
    best_local_score = -1.0
    remote_rows = []
    for row in candidate_rows:
        if is_local_satellite(row):
            local_score, local_row = local_candidate(row)
            if local_row is not None and local_score > best_local_score:
                best_local_score = local_score
                best_local_row = local_row
        else:
            remote_rows.append(row)

    if remote_rows:
        max_workers = min(8, len(remote_rows)) or 1
        executor = ThreadPoolExecutor(max_workers=max_workers)
        healthy_remote_rows = []
        try:
            futures = [executor.submit(remote_candidate, row) for row in remote_rows]
            try:
                for future in as_completed(futures, timeout=SATELLITE_ASSIGN_TIMEOUT_SECONDS):
                    remote_score, remote_row = future.result()
                    if remote_row is not None and remote_score > 0:
                        healthy_remote_rows.append((remote_score, remote_row))
            except FuturesTimeoutError:
                pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        selected_remote_row = select_weighted_satellite(healthy_remote_rows)
        if selected_remote_row is not None:
            return jsonify({"satellite_url": selected_remote_row["url"]})

    if best_local_row is not None and best_local_score > 0:
        return jsonify({"satellite_url": best_local_row["url"]})
    return jsonify({"satellite_url": None})


@app.get("/api/satellites")
def satellite_list():
    require_admin()
    init_db()
    now = time.time()
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM satellites ORDER BY name ASC, id ASC").fetchall()
    return jsonify({"satellites": [satellite_info(row, now) for row in rows]})


@app.get("/api/scaleway/servers")
def scaleway_server_list():
    require_admin()
    scaleway.validate_manage_token()
    payload = scaleway.list_payload()
    servers = payload.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict):
                continue
            server["node_name"] = node_name_for_ip(str(server.get("public_ip", "") or ""))
    return jsonify(payload)


@app.post("/api/scaleway/servers")
def scaleway_server_create_route():
    require_admin()
    scaleway.validate_manage_token()
    try:
        payload = scaleway.create_payload()
    except ScalewayAPIError as exc:
        abort(exc.status_code, exc.message)
    return jsonify(payload), 201


@app.delete("/api/scaleway/servers/<server_id>")
def scaleway_server_delete_route(server_id: str):
    require_admin()
    scaleway.validate_manage_token()
    try:
        payload = scaleway.delete_payload(server_id)
    except ScalewayAPIError as exc:
        abort(exc.status_code, exc.message)
    return jsonify(payload)


_SCHEDULE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCHEDULE_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def load_schedule_entries() -> list[dict]:
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        abort(500, "Schedule file could not be read")
    return data if isinstance(data, list) else []


def validate_schedule_payload(entries) -> list[dict]:
    if not isinstance(entries, list):
        abort(400, "Expected a list of schedule entries")
    cleaned: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            abort(400, f"Entry {index + 1} is not an object")
        date = str(entry.get("date", "")).strip()
        start = str(entry.get("startTime", "")).strip()
        end = str(entry.get("endTime", "")).strip()
        title = str(entry.get("title", "")).strip()
        if not _SCHEDULE_DATE_RE.match(date):
            abort(400, f"Entry {index + 1}: date must be YYYY-MM-DD")
        if not _SCHEDULE_TIME_RE.match(start) or not _SCHEDULE_TIME_RE.match(end):
            abort(400, f"Entry {index + 1}: times must be HH:MM")
        if not title:
            abort(400, f"Entry {index + 1}: title is required")
        cleaned.append(
            {"date": date, "startTime": start, "endTime": end, "title": title}
        )
    cleaned.sort(key=lambda item: (item["date"], item["startTime"]))
    return cleaned


@app.get("/api/schedule")
def schedule_get():
    require_admin()
    return jsonify({"entries": load_schedule_entries(), "name": SCHEDULE_NAME})


@app.post("/api/schedule")
def schedule_save():
    require_admin()
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, "Invalid JSON body")
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    cleaned = validate_schedule_payload(entries)
    target = Path(SCHEDULE_FILE)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, target)
    except OSError as exc:
        abort(500, f"Schedule file could not be written: {exc}")
    return jsonify({"entries": cleaned})


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
