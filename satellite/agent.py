"""
Satellite agent — registers with the main streaming server and sends periodic
heartbeats with local capacity stats (CPU, bandwidth, viewer count).

Env vars:
  MAIN_SERVER_URL          Base URL of the main server (e.g. http://main:5000)
  SATELLITE_API_KEY        Shared secret for satellite auth
  SATELLITE_NAME           Human-readable name for this satellite
  SATELLITE_PUBLIC_URL     Public HLS base URL viewers will connect to
  SATELLITE_MAX_VIEWERS    Max viewer capacity (default 200)
  HEARTBEAT_INTERVAL       Seconds between heartbeats (overridden by server)
  NGINX_STATUS_URL         Local nginx stub_status URL (default http://127.0.0.1:80/nginx-status)
  HLS_ACCESS_LOG           Path to nginx HLS access log (IP + msec format); if set, used instead of stub_status
  HLS_VIEWER_WINDOW        Seconds of rolling window for unique-IP counting (default 15)
"""

import os
import sys
import signal
import time
import logging
import re
import base64
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests
import psutil

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [satellite] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("satellite-agent")

MAIN_SERVER_URL = os.environ.get("MAIN_SERVER_URL", "").rstrip("/")
API_KEY = os.environ.get("SATELLITE_API_KEY", "")
SATELLITE_NAME = os.environ.get("SATELLITE_NAME", "satellite-1")
SATELLITE_PUBLIC_URL = os.environ.get("SATELLITE_PUBLIC_URL", "")
MAX_VIEWERS = int(os.environ.get("SATELLITE_MAX_VIEWERS", "200"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "10"))
NGINX_STATUS_URL = os.environ.get("NGINX_STATUS_URL", "http://127.0.0.1:80/nginx-status")
HLS_ACCESS_LOG = os.environ.get("HLS_ACCESS_LOG", "").strip()
HLS_VIEWER_WINDOW = int(os.environ.get("HLS_VIEWER_WINDOW", "15"))
HLS_INTERNAL_VIEWER_PREFIX = "__internal__:"
CADDY_CERTIFICATES_DIR = os.environ.get(
    "CADDY_CERTIFICATES_DIR",
    "/var/lib/caddy/.local/share/caddy/certificates",
).strip()
CERT_SYNC_RETRY_SECONDS = int(os.environ.get("CERT_SYNC_RETRY_SECONDS", "30"))
CERT_SYNC_INTERVAL = int(os.environ.get("CERT_SYNC_INTERVAL", "3600"))
CERT_SYNC_MAX_FILE_BYTES = 1024 * 1024
CERT_SYNC_MAX_TOTAL_BYTES = 5 * 1024 * 1024

satellite_id = None
running = True
last_cert_sync_attempt = 0.0
last_uploaded_cert_fingerprint = ""


def get_active_connections():
    """Parse nginx stub_status to get current active connections."""
    try:
        resp = requests.get(NGINX_STATUS_URL, timeout=2)
        resp.raise_for_status()
        match = re.search(r"Active connections:\s*(\d+)", resp.text)
        if match:
            return max(0, int(match.group(1)) - 1)  # subtract agent's own conn
    except Exception:
        pass
    return 0


def is_internal_viewer_id(viewer_id):
    normalized = str(viewer_id or "").strip()
    return bool(normalized) and normalized.startswith(HLS_INTERNAL_VIEWER_PREFIX)


def parse_hls_log_line(line):
    text = str(line or "").strip()
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


def get_hls_viewer_count():
    """Count unique viewer IPs in HLS access log within rolling window.

    Satellite-proxied requests can be marked with an extra log field so the
    origin agent ignores edge fetches instead of counting them as direct viewers.
    Falls back to stub_status only when log-based counting is unavailable.
    """
    if not HLS_ACCESS_LOG or not os.path.exists(HLS_ACCESS_LOG):
        return get_active_connections()
    now = time.time()
    cutoff = now - HLS_VIEWER_WINDOW
    unique_ips = set()
    try:
        with open(HLS_ACCESS_LOG, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            offset = max(0, file_size - 65536)  # read last 64 KB
            f.seek(offset)
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        if offset > 0:
            lines = lines[1:]  # skip potentially incomplete first line
        for line in lines:
            parsed = parse_hls_log_line(line)
            if not parsed:
                continue
            timestamp, viewer_id, via_satellite_proxy = parsed
            if timestamp < cutoff or via_satellite_proxy or not viewer_id:
                continue
            unique_ips.add(viewer_id)
    except Exception:
        return get_active_connections()
    return len(unique_ips)


def get_bandwidth_mbps():
    """Estimate current network throughput in Mbps using a 1-second sample."""
    try:
        net1 = psutil.net_io_counters()
        time.sleep(1)
        net2 = psutil.net_io_counters()
        bytes_sent = net2.bytes_sent - net1.bytes_sent
        return round(bytes_sent * 8 / 1_000_000, 2)
    except Exception:
        return 0


def satellite_public_host():
    return (urlparse(SATELLITE_PUBLIC_URL).hostname or "").strip().lower()


def discover_caddy_certificate_bundle():
    host = satellite_public_host()
    if not host or not CADDY_CERTIFICATES_DIR:
        return None
    base = Path(CADDY_CERTIFICATES_DIR)
    if not base.exists():
        return None
    candidates = []
    try:
        candidates = [
            path for path in base.glob(f"*/{host}/{host}.crt")
            if path.is_file()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    cert_path = max(candidates, key=lambda path: path.stat().st_mtime)
    cert_dir = cert_path.parent
    key_path = cert_dir / f"{host}.key"
    if not key_path.is_file():
        return None

    files = {}
    digest = hashlib.sha256()
    total = 0
    try:
        for path in sorted(cert_dir.iterdir()):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if not data or len(data) > CERT_SYNC_MAX_FILE_BYTES:
                continue
            total += len(data)
            if total > CERT_SYNC_MAX_TOTAL_BYTES:
                log.warning("Certificate cache upload skipped: bundle too large")
                return None
            relpath = path.relative_to(base).as_posix()
            files[relpath] = base64.b64encode(data).decode("ascii")
            digest.update(relpath.encode("utf-8"))
            digest.update(b"\0")
            digest.update(data)
    except OSError as exc:
        log.warning("Certificate cache scan failed: %s", exc)
        return None

    if not files:
        return None
    return host, digest.hexdigest(), files


def maybe_sync_caddy_certificate():
    global last_cert_sync_attempt, last_uploaded_cert_fingerprint
    now = time.time()
    interval = CERT_SYNC_INTERVAL if last_uploaded_cert_fingerprint else CERT_SYNC_RETRY_SECONDS
    if now - last_cert_sync_attempt < max(5, interval):
        return
    last_cert_sync_attempt = now

    bundle = discover_caddy_certificate_bundle()
    if not bundle:
        return
    host, fingerprint, files = bundle
    if fingerprint == last_uploaded_cert_fingerprint:
        return

    url = f"{MAIN_SERVER_URL}/api/satellite/cert-cache/upload"
    payload = {
        "api_key": API_KEY,
        "name": SATELLITE_NAME,
        "public_url": SATELLITE_PUBLIC_URL,
        "host": host,
        "fingerprint": fingerprint,
        "files": files,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        last_uploaded_cert_fingerprint = fingerprint
        log.info("Uploaded Caddy certificate cache for %s (%d files)", host, len(files))
    except Exception as exc:
        log.warning("Certificate cache upload failed: %s", exc)


def register():
    """Register this satellite with the main server. Returns satellite ID."""
    url = f"{MAIN_SERVER_URL}/api/satellite/register"
    payload = {
        "api_key": API_KEY,
        "name": SATELLITE_NAME,
        "url": SATELLITE_PUBLIC_URL,
        "capacity_max_viewers": MAX_VIEWERS,
    }
    while running:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            sat_id = data["id"]
            interval = data.get("heartbeat_interval", HEARTBEAT_INTERVAL)
            log.info("Registered as %s (interval=%ds)", sat_id, interval)
            return sat_id, interval
        except Exception as exc:
            log.warning("Registration failed: %s — retrying in 5s", exc)
            time.sleep(5)
    return None, HEARTBEAT_INTERVAL


def heartbeat(sat_id, interval):
    """Send periodic heartbeats to the main server."""
    url = f"{MAIN_SERVER_URL}/api/satellite/{sat_id}/heartbeat"
    while running:
        cpu = psutil.cpu_percent(interval=0)
        viewers = get_hls_viewer_count()
        bandwidth = get_bandwidth_mbps()
        payload = {
            "api_key": API_KEY,
            "cpu_percent": cpu,
            "bandwidth_mbps": bandwidth,
            "viewer_count": viewers,
            "capacity_max_viewers": MAX_VIEWERS,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 404:
                log.warning("Server lost registration, re-registering…")
                return False  # trigger re-register
            resp.raise_for_status()
            maybe_sync_caddy_certificate()
            data = resp.json()
            stream = data.get("stream_active", False)
            log.info(
                "Heartbeat OK — viewers=%d cpu=%.1f%% bw=%.2f Mbps stream=%s",
                viewers, cpu, bandwidth, stream,
            )
        except Exception as exc:
            log.warning("Heartbeat failed: %s", exc)
        time.sleep(interval)
    return True


def deregister(sat_id):
    """Gracefully deregister on shutdown."""
    if not sat_id:
        return
    url = f"{MAIN_SERVER_URL}/api/satellite/{sat_id}"
    try:
        requests.delete(url, json={"api_key": API_KEY}, timeout=5)
        log.info("Deregistered %s", sat_id)
    except Exception as exc:
        log.warning("Deregister failed: %s", exc)


def shutdown_handler(signum, frame):
    global running
    log.info("Received signal %s, shutting down…", signum)
    running = False


def main():
    global satellite_id

    if not MAIN_SERVER_URL:
        log.error("MAIN_SERVER_URL is required")
        sys.exit(1)
    if not API_KEY:
        log.error("SATELLITE_API_KEY is required")
        sys.exit(1)
    if not SATELLITE_PUBLIC_URL:
        log.error("SATELLITE_PUBLIC_URL is required")
        sys.exit(1)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    log.info(
        "Starting satellite agent: name=%s url=%s main=%s",
        SATELLITE_NAME, SATELLITE_PUBLIC_URL, MAIN_SERVER_URL,
    )

    while running:
        satellite_id, interval = register()
        if not satellite_id:
            break
        finished = heartbeat(satellite_id, interval)
        if finished:
            break
        # If heartbeat returns False, re-register

    deregister(satellite_id)
    log.info("Agent stopped")


if __name__ == "__main__":
    main()
