import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from flask import abort, request

SCW_API_LOG_PATH = Path(
    os.getenv("SCW_API_LOG_PATH", "").strip()
    or str(Path(__file__).resolve().with_name("scaleway-api.log"))
)
SERVER_TYPE_CATALOG = (
    {
        "name": "STARDUST1-S",
        "price_hour": "€0.00015/hour",
        "vcpus": 1,
        "memory": "1 GB",
        "bandwidth": "100 Mbps",
    },
    {
        "name": "DEV1-S",
        "price_hour": "€0.0088/hour",
        "vcpus": 2,
        "memory": "2 GB",
        "bandwidth": "200 Mbps",
    },
    {
        "name": "DEV1-M",
        "price_hour": "€0.0198/hour",
        "vcpus": 3,
        "memory": "4 GB",
        "bandwidth": "300 Mbps",
    },
    {
        "name": "DEV1-L",
        "price_hour": "€0.042/hour",
        "vcpus": 4,
        "memory": "8 GB",
        "bandwidth": "400 Mbps",
    },
    {
        "name": "DEV1-XL",
        "price_hour": "€0.0638/hour",
        "vcpus": 4,
        "memory": "12 GB",
        "bandwidth": "500 Mbps",
    },
)

ZONE_OPTIONS = (
    {"id": "fr-par-1", "label": "fr-par-1"},
    {"id": "nl-ams-1", "label": "nl-ams-1"},
    {"id": "pl-waw-2", "label": "pl-waw-2"},
)


@dataclass(frozen=True)
class ScalewayConfig:
    api_base_url: str
    access_key: str
    secret_key: str
    default_organization_id: str
    default_project_id: str
    default_zone: str
    default_commercial_type: str
    default_image: str
    default_root_volume_type: str
    default_root_volume_size_gb: int
    server_name_prefix: str
    manage_token: str
    server_limit: int
    allowed_zones: tuple[str, ...]
    managed_tags: tuple[str, ...]
    satellite_bootstrap_dir: str
    streaming_host: str
    public_origin_url: str
    satellite_api_key: str
    satellite_bootstrap_token: str

    @classmethod
    def from_env(
        cls,
        streaming_host: str,
        public_origin_url: str,
        satellite_api_key: str,
        satellite_bootstrap_token: str,
    ) -> "ScalewayConfig":
        def env_default(key: str, default: str) -> str:
            value = os.getenv(key, "").strip()
            return value or default

        def env_int(key: str, default: int, minimum: int) -> int:
            raw = os.getenv(key, "").strip()
            if not raw:
                return default
            try:
                return max(minimum, int(raw))
            except ValueError:
                return default

        try:
            server_limit = max(1, min(5, int(os.getenv("SCW_SERVER_LIMIT", "5"))))
        except ValueError:
            server_limit = 5
        return cls(
            api_base_url=os.getenv("SCW_API_BASE_URL", "https://api.scaleway.com").strip().rstrip("/"),
            access_key=os.getenv("SCW_ACCESS_KEY", "").strip(),
            secret_key=os.getenv("SCW_SECRET_KEY", "").strip(),
            default_organization_id=os.getenv("SCW_DEFAULT_ORGANIZATION_ID", "").strip(),
            default_project_id=(
                os.getenv("SCW_DEFAULT_PROJECT_ID", "").strip()
                or os.getenv("SCW_PROJECT_ID", "").strip()
            ),
            default_zone=env_default("SCW_DEFAULT_ZONE", "fr-par-1"),
            default_commercial_type=env_default("SCW_DEFAULT_COMMERCIAL_TYPE", "GP1-S"),
            default_image=env_default("SCW_DEFAULT_IMAGE", "ubuntu_noble"),
            default_root_volume_type=env_default("SCW_ROOT_VOLUME_TYPE", "l_ssd"),
            default_root_volume_size_gb=env_int("SCW_ROOT_VOLUME_SIZE_GB", 10, 10),
            server_name_prefix=env_default("SCW_SERVER_NAME_PREFIX", "instance"),
            manage_token=os.getenv("SCW_MANAGE_TOKEN", "").strip(),
            server_limit=server_limit,
            allowed_zones=tuple(option["id"] for option in ZONE_OPTIONS),
            managed_tags=("streaming-satellite", "managed-by-main"),
            satellite_bootstrap_dir=(
                os.getenv("SATELLITE_BOOTSTRAP_DIR", "/bootstrap/satellite").strip()
                or "/bootstrap/satellite"
            ),
            streaming_host=streaming_host.strip(),
            public_origin_url=public_origin_url.strip(),
            satellite_api_key=satellite_api_key.strip(),
            satellite_bootstrap_token=satellite_bootstrap_token.strip(),
        )


class ScalewayAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ScalewayManager:
    def __init__(
        self,
        config: ScalewayConfig,
        connect_db: Callable[[], sqlite3.Connection],
        init_db: Callable[[], None],
        shorten: Callable[[object, int], str | None],
        normalize_ip_address: Callable[[str], str],
        request_external_base_url: Callable[[], str],
    ):
        self.config = config
        self._connect_db = connect_db
        self._init_db = init_db
        self._shorten = shorten
        self._normalize_ip_address = normalize_ip_address
        self._request_external_base_url = request_external_base_url
        self._logger = build_scaleway_logger()

    def ensure_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_scaleway_servers (
                server_id TEXT PRIMARY KEY,
                zone TEXT NOT NULL,
                name TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    def feature_enabled(self) -> bool:
        return bool(
            self.config.manage_token
            and self.config.secret_key
            and self.config.default_project_id
        )

    def default_config(self) -> dict[str, object]:
        return {
            "zone": self.config.default_zone,
            "commercial_type": self.config.default_commercial_type,
            "image": self.config.default_image,
            "root_volume_type": self.config.default_root_volume_type,
            "root_volume_size_gb": self.config.default_root_volume_size_gb,
            "name_prefix": self.config.server_name_prefix,
            "server_limit": self.config.server_limit,
            "zones": list(self.config.allowed_zones),
            "zone_options": self.available_zones(),
            "server_types": self.available_server_types(),
        }

    def validate_manage_token(self) -> None:
        if not self.feature_enabled():
            abort(503, "Scaleway management is not configured")

    def api_request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
    ) -> object | None:
        url = f"{self.config.api_base_url}{path}"
        headers = {
            "X-Auth-Token": self.config.secret_key,
            "Accept": "application/json",
        }
        body = raw_body
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        if body is not None:
            headers["Content-Type"] = content_type
        request_id = f"scw-{time.time_ns()}"
        self._logger.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request_id,
                    "method": method.upper(),
                    "url": url,
                    "headers": self._sanitize_headers(headers),
                    "payload": self._sanitize_log_value(payload),
                    "raw_body": self._sanitize_raw_body(raw_body, content_type),
                },
                ensure_ascii=True,
            )
        )
        request_obj = Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with urlopen(request_obj, timeout=20) as response:
                response_body = response.read()
                response_text = response_body.decode("utf-8", errors="replace") if response_body else ""
                response_headers = dict(response.getheaders())
                content_type = str(response_headers.get("Content-Type") or response_headers.get("content-type") or "").lower()
                parsed_body: object | None = None
                if response_text:
                    if "json" in content_type:
                        try:
                            parsed_body = json.loads(response_text)
                        except json.JSONDecodeError:
                            parsed_body = response_text
                    else:
                        parsed_body = response_text
                self._logger.info(
                    json.dumps(
                        {
                            "event": "response",
                            "request_id": request_id,
                            "status": int(getattr(response, "status", 200) or 200),
                            "headers": self._sanitize_headers(response_headers),
                            "body": self._sanitize_log_value(parsed_body),
                        },
                        ensure_ascii=True,
                    )
                )
                if not response_body:
                    return None
                return parsed_body
        except HTTPError as exc:
            details = ""
            error_text = ""
            try:
                error_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_text = ""
            self._logger.error(
                json.dumps(
                    {
                        "event": "response_error",
                        "request_id": request_id,
                        "status": exc.code,
                        "reason": getattr(exc, "reason", ""),
                        "headers": self._sanitize_headers(dict(exc.headers.items()) if exc.headers else {}),
                        "body": self._sanitize_log_value(self._parse_log_body(error_text)),
                    },
                    ensure_ascii=True,
                )
            )
            if error_text:
                try:
                    parsed = json.loads(error_text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    details = self._shorten(
                        parsed.get("message")
                        or parsed.get("type")
                        or parsed.get("details")
                        or error_text,
                        240,
                    ) or ""
                else:
                    details = self._shorten(error_text, 240) or ""
            raise ScalewayAPIError(exc.code, details or f"Scaleway API returned {exc.code}") from exc
        except URLError as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "response_error",
                        "request_id": request_id,
                        "status": 502,
                        "reason": str(exc.reason),
                    },
                    ensure_ascii=True,
                )
            )
            raise ScalewayAPIError(
                502,
                self._shorten(str(exc.reason), 240) or "Scaleway API unreachable",
            ) from exc

    def list_payload(self) -> dict[str, object]:
        rows = self.managed_rows()
        servers = self.visible_servers(rows)
        return {
            "enabled": self.feature_enabled(),
            "defaults": self.default_config(),
            "count": len(servers),
            "managed_count": len(rows),
            "max_servers": self.config.server_limit,
            "servers": servers,
        }

    def create_payload(self) -> dict[str, object]:
        server = self.create_server()
        rows = self.managed_rows()
        return {
            "server": server,
            "count": len(self.visible_servers(rows)),
            "managed_count": len(rows),
            "max_servers": self.config.server_limit,
        }

    def delete_payload(self, server_id: str) -> dict[str, object]:
        row = self.managed_row(server_id)
        if row is None:
            abort(404, "Managed Scaleway server not found")
        zone = str(row["zone"])
        volume_ids = self.server_volume_ids(server_id, zone)
        current_state = self.wait_for_server_state(
            server_id,
            zone,
            {"running", "starting", "stopping", "stopped", "stopped in place"},
        )
        if current_state not in {"stopped", "stopped in place"}:
            self.server_action(zone, server_id, "poweroff")
            self.wait_for_server_state(server_id, zone, {"stopped", "stopped in place"})
        try:
            self.api_request("DELETE", self.instance_path(zone, server_id))
        except ScalewayAPIError as exc:
            if exc.status_code != 404:
                raise
        else:
            self.wait_for_server_deletion(server_id, zone)
        deleted_volumes = self.delete_volumes(zone, volume_ids)
        self.remove_managed_server(server_id)
        rows = self.managed_rows()
        return {
            "deleted": server_id,
            "deleted_volumes": deleted_volumes,
            "count": len(self.visible_servers(rows)),
            "managed_count": len(rows),
            "max_servers": self.config.server_limit,
        }

    def create_server(self) -> dict[str, object]:
        self._init_db()
        payload = self.parse_server_payload()
        with self._connect_db() as conn:
            count = self.active_managed_count(conn)
        if count >= self.config.server_limit:
            abort(409, f"Maximum of {self.config.server_limit} Scaleway servers reached")
        if not payload["name"]:
            payload["name"] = self.default_server_name()
        create_payload = {
            "name": payload["name"],
            "project": self.config.default_project_id,
            "commercial_type": payload["commercial_type"],
            "image": payload["image"],
            "dynamic_ip_required": True,
            "volumes": {
                "0": {
                    "size": payload["root_volume_size_gb"] * 1_000_000_000,
                    "volume_type": payload["root_volume_type"],
                }
            },
            "tags": list(self.config.managed_tags),
        }
        created = self.api_request("POST", self.instance_path(payload["zone"]), create_payload)
        server = created.get("server") if isinstance(created, dict) else None
        if not isinstance(server, dict):
            abort(502, "Unexpected Scaleway create response")
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            abort(502, "Scaleway create response did not include a server ID")
        current_state = self.wait_for_server_state(
            server_id,
            payload["zone"],
            {"running", "starting", "stopped", "stopping", "stopped in place"},
        )
        if current_state not in {"stopped", "stopped in place"}:
            self.server_action(payload["zone"], server_id, "poweroff")
            self.wait_for_server_state(server_id, payload["zone"], {"stopped", "stopped in place"})
        cloud_init = self.render_cloud_init().encode("utf-8")
        last_error: ScalewayAPIError | None = None
        for delay_seconds in (0, 1, 2):
            if delay_seconds:
                time.sleep(delay_seconds)
            try:
                self.api_request(
                    "PATCH",
                    f"{self.instance_path(payload['zone'], server_id)}/user_data/cloud-init",
                    raw_body=cloud_init,
                    content_type="text/plain",
                )
                self.verify_cloud_init(server_id, payload["zone"], cloud_init.decode("utf-8"))
                last_error = None
                break
            except ScalewayAPIError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        self.server_action(payload["zone"], server_id, "poweron")
        self.wait_for_server_state(server_id, payload["zone"], {"running", "starting"})
        self.record_managed_server(
            server_id,
            payload["zone"],
            payload["name"],
            self.config.default_project_id,
        )
        return self.server_summary(
            {
                "server_id": server_id,
                "zone": payload["zone"],
                "name": payload["name"],
                "project_id": self.config.default_project_id,
                "created_at": time.time(),
            }
        )

    def render_cloud_init(self) -> str:
        bootstrap_token = self.config.satellite_bootstrap_token or self.config.satellite_api_key
        if not bootstrap_token:
            abort(500, "Satellite bootstrap token is not configured")
        host = self.bootstrap_host()
        cloud_init_path = Path(self.config.satellite_bootstrap_dir) / "cloud-init.example.yaml"
        if not cloud_init_path.exists():
            fallback_path = Path(__file__).resolve().parent.parent / "satellite" / "cloud-init.example.yaml"
            cloud_init_path = fallback_path
        text = cloud_init_path.read_text(encoding="utf-8")
        text = re.sub(
            r'^(\s*STREAMING_HOST=)"[^"]*"$',
            lambda match: f'{match.group(1)}"{host}"',
            text,
            flags=re.MULTILINE,
        )
        text = re.sub(
            r'^(\s*BOOTSTRAP_TOKEN=)"[^"]*"$',
            lambda match: f'{match.group(1)}"{bootstrap_token}"',
            text,
            flags=re.MULTILINE,
        )
        return text

    def bootstrap_host(self) -> str:
        if self.config.streaming_host:
            return self.config.streaming_host
        base_url = self.config.public_origin_url or self._request_external_base_url()
        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        if host:
            return host
        abort(500, "STREAMING_HOST is required for Scaleway bootstrap")

    def parse_server_payload(self) -> dict[str, str]:
        data = request.get_json(silent=True) or {}
        name = self._shorten(data.get("name") or "", 80) or ""
        zone = self.normalize_zone(data.get("zone") or self.config.default_zone)
        commercial_type = self._shorten(
            data.get("commercial_type") or self.config.default_commercial_type,
            40,
        ) or ""
        image = self.config.default_image
        root_volume_type = self._shorten(
            data.get("root_volume_type") or self.config.default_root_volume_type,
            20,
        ) or ""
        try:
            root_volume_size_gb = max(
                10,
                int(data.get("root_volume_size_gb") or self.config.default_root_volume_size_gb),
            )
        except (TypeError, ValueError):
            abort(400, "Invalid root volume size")
        if name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", name):
            abort(400, "Invalid server name")
        if not commercial_type:
            abort(400, "Missing Scaleway commercial type")
        if not image:
            abort(400, "Missing Scaleway image")
        if commercial_type not in self.server_type_names():
            abort(400, "Unsupported Scaleway server type")
        if root_volume_type != "l_ssd":
            abort(400, "Only local l_ssd storage is supported here")
        return {
            "name": name,
            "zone": zone,
            "commercial_type": commercial_type,
            "image": image,
            "root_volume_type": root_volume_type,
            "root_volume_size_gb": root_volume_size_gb,
        }

    def normalize_zone(self, value: str) -> str:
        zone = str(value or "").strip().lower()
        if zone not in self.config.allowed_zones:
            abort(400, "Invalid Scaleway zone")
        return zone

    def active_managed_count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) AS count FROM managed_scaleway_servers").fetchone()
        return int(row["count"]) if row else 0

    def default_server_name(self, index: int | None = None) -> str:
        if index is None or index <= 0:
            index = self.next_server_number()
        return f"{self.config.server_name_prefix}{index}"

    def next_server_number(self) -> int:
        highest = 0
        pattern = re.compile(rf"^{re.escape(self.config.server_name_prefix)}(\d+)$")
        for row in self.managed_rows():
            name = str(row["name"] or "")
            match = pattern.match(name)
            if not match:
                continue
            try:
                highest = max(highest, int(match.group(1)))
            except ValueError:
                continue
        return highest + 1

    def record_managed_server(
        self,
        server_id: str,
        zone: str,
        name: str,
        project_id: str,
    ) -> None:
        current = time.time()
        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT INTO managed_scaleway_servers(
                    server_id,
                    zone,
                    name,
                    project_id,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    zone = excluded.zone,
                    name = excluded.name,
                    project_id = excluded.project_id,
                    updated_at = excluded.updated_at
                """,
                (server_id, zone, name, project_id, current, current),
            )

    def remove_managed_server(self, server_id: str) -> None:
        with self._connect_db() as conn:
            conn.execute("DELETE FROM managed_scaleway_servers WHERE server_id = ?", (server_id,))

    def managed_rows(self) -> list[sqlite3.Row]:
        with self._connect_db() as conn:
            return conn.execute(
                "SELECT * FROM managed_scaleway_servers ORDER BY created_at DESC, server_id DESC"
            ).fetchall()

    def managed_row(self, server_id: str) -> sqlite3.Row | None:
        with self._connect_db() as conn:
            return conn.execute(
                "SELECT * FROM managed_scaleway_servers WHERE server_id = ?",
                (server_id,),
            ).fetchone()

    def instance_path(self, zone: str, server_id: str = "") -> str:
        base = f"/instance/v1/zones/{zone}/servers"
        if server_id:
            return f"{base}/{server_id}"
        return base

    def volume_path(self, zone: str, volume_id: str = "") -> str:
        base = f"/instance/v1/zones/{zone}/volumes"
        if volume_id:
            return f"{base}/{volume_id}"
        return base

    def server_action(self, zone: str, server_id: str, action: str) -> None:
        self.api_request(
            "POST",
            f"{self.instance_path(zone, server_id)}/action",
            payload={"action": action},
        )

    def wait_for_server_state(
        self,
        server_id: str,
        zone: str,
        expected_states: set[str],
        timeout_seconds: float = 90.0,
        poll_interval_seconds: float = 2.0,
    ) -> str:
        deadline = time.time() + timeout_seconds
        normalized_expected = {state.strip().lower() for state in expected_states if state.strip()}
        last_state = ""
        while time.time() < deadline:
            payload = self.api_request("GET", self.instance_path(zone, server_id))
            server = payload.get("server") if isinstance(payload, dict) else None
            if isinstance(server, dict):
                last_state = str(server.get("state") or "").strip().lower()
                if last_state in normalized_expected:
                    return last_state
            time.sleep(poll_interval_seconds)
        expected_label = ", ".join(sorted(normalized_expected)) or "unknown"
        raise ScalewayAPIError(
            504,
            f"Timed out waiting for server {server_id} to reach state: {expected_label} (last state: {last_state or 'unknown'})",
        )

    def wait_for_server_deletion(
        self,
        server_id: str,
        zone: str,
        timeout_seconds: float = 90.0,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                payload = self.api_request("GET", self.instance_path(zone, server_id))
            except ScalewayAPIError as exc:
                if exc.status_code == 404:
                    return
                raise
            server = payload.get("server") if isinstance(payload, dict) else None
            if not isinstance(server, dict):
                return
            time.sleep(poll_interval_seconds)
        raise ScalewayAPIError(504, f"Timed out waiting for server {server_id} deletion")

    def server_volume_ids(self, server_id: str, zone: str) -> list[str]:
        try:
            payload = self.api_request("GET", self.instance_path(zone, server_id))
        except ScalewayAPIError:
            return []
        server = payload.get("server") if isinstance(payload, dict) else None
        if not isinstance(server, dict):
            return []
        volumes = server.get("volumes")
        if not isinstance(volumes, dict):
            return []
        volume_ids: list[str] = []
        for volume in volumes.values():
            if not isinstance(volume, dict):
                continue
            volume_id = str(volume.get("id") or "").strip()
            if volume_id:
                volume_ids.append(volume_id)
        return volume_ids

    def delete_volumes(self, zone: str, volume_ids: list[str]) -> list[str]:
        deleted: list[str] = []
        for volume_id in volume_ids:
            try:
                self.api_request("DELETE", self.volume_path(zone, volume_id))
            except ScalewayAPIError as exc:
                if exc.status_code == 404:
                    continue
                raise
            deleted.append(volume_id)
        return deleted

    def verify_cloud_init(self, server_id: str, zone: str, expected_cloud_init: str) -> None:
        payload = self.api_request(
            "GET",
            f"{self.instance_path(zone, server_id)}/user_data/cloud-init",
        )
        if isinstance(payload, dict):
            content = str(payload.get("content") or "")
        elif isinstance(payload, str):
            content = payload
        else:
            raise ScalewayAPIError(502, "Unable to verify Scaleway cloud-init payload")
        if content.strip() != expected_cloud_init.strip():
            raise ScalewayAPIError(502, "Scaleway cloud-init payload does not match the expected script")

    def public_ip(self, server: dict[str, object]) -> str:
        public_ips = server.get("public_ips")
        if isinstance(public_ips, list):
            for item in public_ips:
                if not isinstance(item, dict):
                    continue
                address = self._normalize_ip_address(str(item.get("address", "") or ""))
                if address:
                    return address
        legacy_ip = server.get("public_ip")
        if isinstance(legacy_ip, dict):
            return self._normalize_ip_address(str(legacy_ip.get("address", "") or ""))
        return ""

    def visible_servers(self, managed_rows: list[sqlite3.Row]) -> list[dict[str, object]]:
        managed_by_id = {str(row["server_id"]): row for row in managed_rows}
        visible_by_id: dict[str, dict[str, object]] = {}
        for zone in self.config.allowed_zones:
            for server in self.list_zone_servers(zone):
                project_id = str(server.get("project") or server.get("project_id") or "").strip()
                if project_id != self.config.default_project_id:
                    continue
                server_id = str(server.get("id") or "").strip()
                if not server_id:
                    continue
                visible_by_id[server_id] = self.server_summary_from_remote(
                    server,
                    zone,
                    managed=server_id in managed_by_id,
                )
        for server_id, row in managed_by_id.items():
            if server_id not in visible_by_id:
                visible_by_id[server_id] = self.server_summary(row, managed=True)
        return sorted(
            visible_by_id.values(),
            key=lambda item: (
                str(item.get("name") or "").lower(),
                str(item.get("zone") or "").lower(),
                str(item.get("id") or "").lower(),
            ),
        )

    def list_zone_servers(self, zone: str) -> list[dict[str, object]]:
        servers: list[dict[str, object]] = []
        page = 1
        per_page = 100
        while True:
            payload = self.api_request(
                "GET",
                f"{self.instance_path(zone)}?page={page}&per_page={per_page}",
            )
            items = payload.get("servers") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                break
            normalized = [item for item in items if isinstance(item, dict)]
            servers.extend(normalized)
            if len(normalized) < per_page:
                break
            page += 1
        return servers

    def server_summary(self, row: sqlite3.Row | dict[str, object], managed: bool = False) -> dict[str, object]:
        summary = {
            "id": str(row["server_id"]),
            "zone": str(row["zone"]),
            "name": str(row["name"]),
            "project_id": str(row["project_id"] or ""),
            "created_at": float(row["created_at"]),
            "state": "unknown",
            "public_ip": "",
            "error": "",
            "managed": managed,
        }
        try:
            payload = self.api_request("GET", self.instance_path(summary["zone"], summary["id"]))
        except ScalewayAPIError as exc:
            if exc.status_code == 404:
                summary["state"] = "deleted"
                summary["error"] = "Server no longer exists in Scaleway"
                return summary
            summary["state"] = "error"
            summary["error"] = exc.message
            return summary
        server = payload.get("server") if isinstance(payload, dict) else None
        if not isinstance(server, dict):
            summary["state"] = "error"
            summary["error"] = "Unexpected Scaleway response"
            return summary
        summary["name"] = self._shorten(server.get("name") or summary["name"], 120) or summary["name"]
        summary["state"] = self._shorten(server.get("state") or "unknown", 40) or "unknown"
        summary["public_ip"] = self.public_ip(server)
        summary["commercial_type"] = self._shorten(server.get("commercial_type") or "", 40) or ""
        summary["allowed_actions"] = (
            server.get("allowed_actions")
            if isinstance(server.get("allowed_actions"), list)
            else []
        )
        return summary

    def server_summary_from_remote(
        self,
        server: dict[str, object],
        zone: str,
        managed: bool = False,
    ) -> dict[str, object]:
        return {
            "id": str(server.get("id") or ""),
            "zone": zone,
            "name": self._shorten(server.get("name") or "", 120) or str(server.get("id") or ""),
            "project_id": str(server.get("project") or server.get("project_id") or ""),
            "created_at": float(server.get("creation_date_ts") or 0),
            "state": self._shorten(server.get("state") or "unknown", 40) or "unknown",
            "public_ip": self.public_ip(server),
            "error": "",
            "managed": managed,
            "commercial_type": self._shorten(server.get("commercial_type") or "", 40) or "",
            "allowed_actions": (
                server.get("allowed_actions")
                if isinstance(server.get("allowed_actions"), list)
                else []
            ),
        }

    def server_type_names(self) -> set[str]:
        return {entry["name"] for entry in SERVER_TYPE_CATALOG}

    def available_server_types(self) -> list[dict[str, object]]:
        return [
            {
                "name": entry["name"],
                "price_hour": entry["price_hour"],
                "vcpus": entry["vcpus"],
                "memory": entry["memory"],
                "bandwidth": entry["bandwidth"],
            }
            for entry in SERVER_TYPE_CATALOG
        ]

    def available_zones(self) -> list[dict[str, str]]:
        return [
            {"id": option["id"], "label": option["label"]}
            for option in ZONE_OPTIONS
            if option["id"] in self.config.allowed_zones
        ]

    def _parse_log_body(self, value: str) -> object:
        text = str(value or "")
        if not text:
            return ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _sanitize_headers(self, headers: dict[str, object]) -> dict[str, object]:
        redacted: dict[str, object] = {}
        for key, value in headers.items():
            if str(key).lower() in {"x-auth-token", "authorization"}:
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = self._sanitize_log_value(value)
        return redacted

    def _sanitize_raw_body(self, raw_body: bytes | None, content_type: str) -> object | None:
        if raw_body is None:
            return None
        try:
            text = raw_body.decode("utf-8", errors="replace")
        except Exception:
            return f"<{len(raw_body)} bytes>"
        if content_type == "application/json":
            return self._sanitize_log_value(self._parse_log_body(text))
        return self._sanitize_log_value(text)

    def _sanitize_log_value(self, value: object) -> object:
        secrets_to_redact = [
            self.config.secret_key,
            self.config.access_key,
            self.config.manage_token,
            self.config.satellite_api_key,
            self.config.satellite_bootstrap_token,
        ]
        if isinstance(value, dict):
            return {str(key): self._sanitize_log_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_log_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_log_value(item) for item in value]
        if value is None:
            return None
        text = str(value)
        for secret in secrets_to_redact:
            if secret:
                text = text.replace(secret, "[redacted]")
        text = re.sub(r'(BOOTSTRAP_TOKEN=")[^"]*(")', r"\1[redacted]\2", text)
        text = re.sub(r"(SATELLITE_API_KEY=)[^\n\r]*", r"\1[redacted]", text)
        return text


def build_scaleway_logger() -> logging.Logger:
    logger = logging.getLogger("scaleway_api")
    if logger.handlers:
        return logger
    SCW_API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(SCW_API_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
