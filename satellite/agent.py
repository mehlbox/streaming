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
"""

import os
import sys
import signal
import time
import logging
import re

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

satellite_id = None
running = True


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
        viewers = get_active_connections()
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
