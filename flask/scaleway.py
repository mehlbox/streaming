import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
HOURLY_PRICE_QUANTUM = Decimal("0.00001")
STORAGE_PRICE_UNIT_GB = Decimal("10")
LOCAL_STORAGE_PRICE_PER_UNIT_HOUR_EUR = Decimal("0.00049")
IPV4_PRICE_HOUR_EUR = Decimal("0.005")
SECONDS_PER_HOUR = Decimal("3600")
LEGACY_BILLING_SEED_SECONDS = 3600.0
MINIMUM_BILLABLE_COMPUTE_SECONDS = 3600.0

SERVER_TYPE_CATALOG = (
    {
        "name": "STARDUST1-S",
        "price_hour_eur": "0.0006",
        "vcpus": 1,
        "memory": "1 GB",
        "bandwidth": "100 Mbps",
    },
    {
        "name": "DEV1-S",
        "price_hour_eur": "0.00898",
        "vcpus": 2,
        "memory": "2 GB",
        "bandwidth": "200 Mbps",
    },
    {
        "name": "DEV1-M",
        "price_hour_eur": "0.0202",
        "vcpus": 3,
        "memory": "4 GB",
        "bandwidth": "300 Mbps",
    },
    {
        "name": "DEV1-L",
        "price_hour_eur": "0.04284",
        "vcpus": 4,
        "memory": "8 GB",
        "bandwidth": "400 Mbps",
    },
    {
        "name": "DEV1-XL",
        "price_hour_eur": "0.06508",
        "vcpus": 4,
        "memory": "12 GB",
        "bandwidth": "500 Mbps",
    },
)


def local_storage_server_type_catalog() -> tuple[dict[str, object], ...]:
    return SERVER_TYPE_CATALOG


def hourly_price_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value or 0))
        except (InvalidOperation, ValueError, TypeError):
            amount = Decimal("0")
    return amount.quantize(HOURLY_PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def format_hourly_price(value: object) -> str:
    amount = hourly_price_decimal(value)
    text = f"{amount:.5f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return f"€{text}/hour"


def format_currency(value: object) -> str:
    amount = hourly_price_decimal(value)
    text = f"{amount:.5f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return f"€{text}"


def timestamp_value(value: object, fallback: float = 0.0) -> float:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        timestamp = fallback
    if timestamp < 0:
        return fallback if fallback > 0 else 0.0
    return timestamp


def clamp_timestamp(value: object, reference_time: float) -> float:
    timestamp = timestamp_value(value)
    if timestamp <= 0:
        return 0.0
    return min(timestamp, max(reference_time, 0.0))


def normalize_state(value: object) -> str:
    return str(value or "").strip().lower()


def is_compute_billed_state(state: object) -> bool:
    normalized = normalize_state(state)
    return normalized not in {"", "stopped", "stopped in place", "deleted", "deleting"}


def is_resource_billed_state(state: object) -> bool:
    return normalize_state(state) not in {"", "deleted", "deleting"}


def normalize_billing_windows(
    raw_windows: object,
    reference_time: float,
) -> list[dict[str, float | None]]:
    if not isinstance(raw_windows, list):
        return []
    windows: list[dict[str, float | None]] = []
    for item in raw_windows:
        if not isinstance(item, dict):
            continue
        started_at = clamp_timestamp(item.get("started_at"), reference_time)
        if started_at <= 0:
            continue
        ended_raw = item.get("ended_at")
        ended_at = None if ended_raw in {None, ""} else clamp_timestamp(ended_raw, reference_time)
        if ended_at is not None and ended_at < started_at:
            ended_at = started_at
        windows.append(
            {
                "started_at": started_at,
                "ended_at": ended_at,
            }
        )
    windows.sort(key=lambda item: (item["started_at"], float(item["ended_at"] or reference_time)))
    normalized_windows: list[dict[str, float | None]] = []
    open_seen = False
    for window in windows:
        if window["ended_at"] is None:
            if open_seen:
                continue
            open_seen = True
        normalized_windows.append(window)
    return normalized_windows


def has_open_billing_window(windows: list[dict[str, float | None]]) -> bool:
    return bool(windows) and windows[-1].get("ended_at") is None


def open_billing_window(windows: list[dict[str, float | None]], started_at: float) -> bool:
    if started_at <= 0 or has_open_billing_window(windows):
        return False
    windows.append({"started_at": float(started_at), "ended_at": None})
    return True


def close_billing_window(
    windows: list[dict[str, float | None]],
    ended_at: float,
    reference_time: float,
) -> bool:
    if not has_open_billing_window(windows):
        return False
    normalized_end = clamp_timestamp(ended_at, reference_time) or max(reference_time, 0.0)
    started_at = float(windows[-1].get("started_at") or 0)
    if normalized_end < started_at:
        normalized_end = started_at
    windows[-1]["ended_at"] = normalized_end
    return True


def billed_cost_for_windows(
    rate_per_hour_eur: object,
    windows: list[dict[str, float | None]],
    reference_time: float,
    minimum_seconds: float = 0.0,
) -> Decimal:
    rate = hourly_price_decimal(rate_per_hour_eur)
    minimum = Decimal(str(minimum_seconds or 0))
    total = Decimal("0")
    for window in windows:
        started_at = clamp_timestamp(window.get("started_at"), reference_time)
        if started_at <= 0:
            continue
        ended_raw = window.get("ended_at")
        ended_at = reference_time if ended_raw in {None, ""} else clamp_timestamp(ended_raw, reference_time)
        if ended_at < started_at:
            ended_at = started_at
        duration_seconds = Decimal(str(ended_at - started_at))
        if duration_seconds <= 0:
            continue
        if minimum > 0 and duration_seconds < minimum:
            duration_seconds = minimum
        total += rate * (duration_seconds / SECONDS_PER_HOUR)
    return hourly_price_decimal(total)


def iter_server_volumes(volumes: object) -> list[dict[str, object]]:
    if isinstance(volumes, dict):
        return [value for value in volumes.values() if isinstance(value, dict)]
    if isinstance(volumes, list):
        return [value for value in volumes if isinstance(value, dict)]
    return []


def format_storage_size(size_gb: Decimal) -> str:
    normalized = size_gb.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{normalized:.2f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return f"{text} GB"


def storage_price_details(
    volumes: object,
    fallback_volume_type: str = "",
    fallback_size_gb: int | None = None,
) -> tuple[Decimal, dict[str, object]]:
    total_size_gb = Decimal("0")
    detected_volume_type = ""
    for volume in iter_server_volumes(volumes):
        if not detected_volume_type:
            detected_volume_type = str(volume.get("volume_type") or volume.get("type") or "").strip().lower()
        raw_size = volume.get("size") or volume.get("size_bytes") or volume.get("size_in_bytes") or 0
        try:
            size_bytes = Decimal(str(raw_size or 0))
        except (InvalidOperation, ValueError, TypeError):
            size_bytes = Decimal("0")
        if size_bytes > 0:
            total_size_gb += size_bytes / Decimal("1000000000")
    if total_size_gb <= 0 and fallback_size_gb:
        try:
            total_size_gb = Decimal(str(fallback_size_gb))
        except (InvalidOperation, ValueError, TypeError):
            total_size_gb = Decimal("0")
    volume_type = detected_volume_type or str(fallback_volume_type or "").strip().lower()
    if total_size_gb <= 0:
        return Decimal("0"), {
            "storage_kind": "-",
            "storage_size_gb": 0.0,
            "storage_size_label": "-",
            "volume_type": volume_type,
            "storage_price_hour_eur": 0.0,
            "storage_price_hour": format_hourly_price(0),
        }
    storage_price = hourly_price_decimal(
        (total_size_gb / STORAGE_PRICE_UNIT_GB) * LOCAL_STORAGE_PRICE_PER_UNIT_HOUR_EUR
    )
    storage_kind = "Local Storage"
    return storage_price, {
        "storage_kind": storage_kind,
        "storage_size_gb": float(total_size_gb.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "storage_size_label": format_storage_size(total_size_gb),
        "volume_type": volume_type,
        "storage_price_hour_eur": float(storage_price),
        "storage_price_hour": format_hourly_price(storage_price),
    }


def server_price_details(
    commercial_type: str,
    public_ip: str = "",
    volumes: object = None,
    fallback_volume_type: str = "",
    fallback_size_gb: int | None = None,
) -> dict[str, object]:
    entry = server_type_entry(commercial_type)
    base_price = hourly_price_decimal(entry.get("price_hour_eur") if entry else 0)
    ipv4_price = hourly_price_decimal(IPV4_PRICE_HOUR_EUR if str(public_ip or "").strip() else 0)
    storage_price, storage_meta = storage_price_details(
        volumes,
        fallback_volume_type=fallback_volume_type,
        fallback_size_gb=fallback_size_gb,
    )
    total_price = hourly_price_decimal(base_price + ipv4_price + storage_price)
    return {
        "base_price_hour_eur": float(base_price),
        "base_price_hour": format_hourly_price(base_price),
        "ipv4_price_hour_eur": float(ipv4_price),
        "ipv4_price_hour": format_hourly_price(ipv4_price),
        **storage_meta,
        "total_price_hour_eur": float(total_price),
        "total_price_hour": format_hourly_price(total_price),
    }


def current_rate_details(
    state: object,
    base_price_hour_eur: object,
    ipv4_price_hour_eur: object,
    storage_price_hour_eur: object,
) -> dict[str, object]:
    base_price = hourly_price_decimal(base_price_hour_eur)
    ipv4_price = hourly_price_decimal(ipv4_price_hour_eur)
    storage_price = hourly_price_decimal(storage_price_hour_eur)
    resource_rate = hourly_price_decimal(ipv4_price + storage_price)
    if not is_resource_billed_state(state):
        resource_rate = hourly_price_decimal(0)
    compute_rate = hourly_price_decimal(base_price if is_compute_billed_state(state) else 0)
    current_rate = hourly_price_decimal(resource_rate + compute_rate)
    return {
        "current_rate_hour_eur": float(current_rate),
        "current_rate_hour": format_hourly_price(current_rate),
    }


def server_type_entry(commercial_type: str) -> dict[str, object] | None:
    normalized_type = str(commercial_type or "").strip()
    if not normalized_type:
        return None
    for entry in SERVER_TYPE_CATALOG:
        if str(entry.get("name") or "") == normalized_type:
            return entry
    return None


def server_type_bandwidth_mbps(commercial_type: str) -> float:
    entry = server_type_entry(commercial_type)
    if entry is None:
        return 0.0
    raw_bandwidth = str(entry.get("bandwidth") or "")
    match = re.search(r"(\d+(?:\.\d+)?)", raw_bandwidth)
    if match is None:
        return 0.0
    return float(match.group(1))

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
    cost_totals_path: str

    @classmethod
    def from_env(
        cls,
        streaming_host: str,
        public_origin_url: str,
        satellite_api_key: str,
        satellite_bootstrap_token: str,
        state_db_path: str,
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
            server_limit = max(1, min(10, int(os.getenv("SCW_SERVER_LIMIT", "10"))))
        except ValueError:
            server_limit = 10
        local_storage_types = local_storage_server_type_catalog()
        default_commercial_type = env_default(
            "SCW_DEFAULT_COMMERCIAL_TYPE",
            str(local_storage_types[0]["name"]),
        )
        if default_commercial_type not in {str(entry["name"]) for entry in local_storage_types}:
            default_commercial_type = str(local_storage_types[0]["name"])
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
            default_commercial_type=default_commercial_type,
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
            cost_totals_path=(
                os.getenv("SCW_COST_TOTALS_PATH", "").strip()
                or str(Path(state_db_path).with_name("scaleway-costs.json"))
            ),
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

    def _pricing_totals_path(self) -> Path:
        return Path(self.config.cost_totals_path)

    def _default_pricing_totals(self, reference_time: float | None = None) -> dict[str, object]:
        return self._build_pricing_totals({}, updated_at=0.0, reference_time=reference_time)

    def _normalize_pricing_instance(
        self,
        server_id: str,
        item: dict[str, object],
        reference_time: float | None = None,
    ) -> dict[str, object]:
        billing_now = timestamp_value(reference_time, time.time())
        base_price = hourly_price_decimal(item.get("base_price_hour_eur") or 0)
        ipv4_price = hourly_price_decimal(item.get("ipv4_price_hour_eur") or 0)
        storage_price = hourly_price_decimal(item.get("storage_price_hour_eur") or 0)
        total_price = hourly_price_decimal(
            item.get("total_price_hour_eur") or (base_price + ipv4_price + storage_price)
        )
        created_at = timestamp_value(item.get("created_at") or 0)
        updated_at = timestamp_value(item.get("updated_at") or 0)
        deleted_at = timestamp_value(item.get("deleted_at") or 0)
        try:
            storage_size_gb = float(item.get("storage_size_gb") or 0)
        except (TypeError, ValueError):
            storage_size_gb = 0.0
        resource_windows = normalize_billing_windows(item.get("resource_windows"), billing_now)
        compute_windows = normalize_billing_windows(item.get("compute_windows"), billing_now)
        current_state = normalize_state(item.get("current_state") or item.get("state") or item.get("last_state") or "")
        seed_billed_cost = hourly_price_decimal(item.get("seed_billed_cost_eur") or 0)
        billed_compute_cost = billed_cost_for_windows(
            base_price,
            compute_windows,
            billing_now,
            minimum_seconds=MINIMUM_BILLABLE_COMPUTE_SECONDS,
        )
        billed_resource_cost = billed_cost_for_windows(
            ipv4_price + storage_price,
            resource_windows,
            billing_now,
        )
        billed_total_cost = hourly_price_decimal(seed_billed_cost + billed_compute_cost + billed_resource_cost)
        current_rate = hourly_price_decimal(0)
        if has_open_billing_window(resource_windows):
            current_rate += hourly_price_decimal(ipv4_price + storage_price)
        if has_open_billing_window(compute_windows):
            current_rate += base_price
        current_rate = hourly_price_decimal(current_rate)
        has_billing_windows = bool(resource_windows or compute_windows)
        legacy_estimate = bool(
            item.get("legacy_estimate")
            or (
                not has_billing_windows
                and seed_billed_cost <= 0
                and deleted_at <= 0
                and "total_price_hour_eur" in item
            )
        )
        return {
            "name": str(item.get("name") or server_id),
            "zone": str(item.get("zone") or ""),
            "commercial_type": str(item.get("commercial_type") or ""),
            "created_at": created_at,
            "updated_at": updated_at,
            "deleted_at": deleted_at,
            "current_state": current_state,
            "storage_kind": str(item.get("storage_kind") or "-"),
            "storage_size_gb": storage_size_gb,
            "storage_size_label": str(item.get("storage_size_label") or "-"),
            "base_price_hour_eur": float(base_price),
            "base_price_hour": format_hourly_price(base_price),
            "ipv4_price_hour_eur": float(ipv4_price),
            "ipv4_price_hour": format_hourly_price(ipv4_price),
            "storage_price_hour_eur": float(storage_price),
            "storage_price_hour": format_hourly_price(storage_price),
            "total_price_hour_eur": float(total_price),
            "total_price_hour": format_hourly_price(total_price),
            "resource_windows": resource_windows,
            "compute_windows": compute_windows,
            "legacy_estimate": legacy_estimate,
            "seed_billed_cost_eur": float(seed_billed_cost),
            "seed_billed_cost": format_currency(seed_billed_cost),
            "billed_compute_cost_eur": float(billed_compute_cost),
            "billed_compute_cost": format_currency(billed_compute_cost),
            "billed_resource_cost_eur": float(billed_resource_cost),
            "billed_resource_cost": format_currency(billed_resource_cost),
            "billed_cost_eur": float(billed_total_cost),
            "billed_cost": format_currency(billed_total_cost),
            "current_rate_hour_eur": float(current_rate),
            "current_rate_hour": format_hourly_price(current_rate),
        }

    def _build_pricing_totals(
        self,
        instances: dict[str, dict[str, object]],
        updated_at: float = 0.0,
        reference_time: float | None = None,
    ) -> dict[str, object]:
        billing_now = timestamp_value(reference_time, time.time())
        normalized_instances = {
            server_id: self._normalize_pricing_instance(server_id, item, reference_time=billing_now)
            for server_id, item in instances.items()
            if str(server_id or "").strip()
        }
        overall_billed_cost = hourly_price_decimal(0)
        for item in normalized_instances.values():
            overall_billed_cost += hourly_price_decimal(item.get("billed_cost_eur") or 0)
        overall_billed_cost = hourly_price_decimal(overall_billed_cost)
        return {
            "currency": "EUR",
            "instance_count": len(normalized_instances),
            "instances": dict(sorted(normalized_instances.items())),
            "overall_billed_cost_eur": float(overall_billed_cost),
            "overall_billed_cost": format_currency(overall_billed_cost),
            "overall_total_price_hour_eur": float(overall_billed_cost),
            "overall_total_price_hour": format_currency(overall_billed_cost),
            "updated_at": timestamp_value(updated_at),
        }

    def _load_pricing_totals(self, reference_time: float | None = None) -> dict[str, object]:
        path = self._pricing_totals_path()
        if not path.exists():
            return self._default_pricing_totals(reference_time=reference_time)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_pricing_totals(reference_time=reference_time)
        if not isinstance(payload, dict):
            return self._default_pricing_totals(reference_time=reference_time)
        raw_instances = payload.get("instances")
        instances: dict[str, dict[str, object]] = {}
        if isinstance(raw_instances, dict):
            for server_id, item in raw_instances.items():
                if not isinstance(item, dict):
                    continue
                normalized_id = str(server_id or "").strip()
                if not normalized_id:
                    continue
                instances[normalized_id] = item
        updated_at = timestamp_value(payload.get("updated_at") or 0)
        return self._build_pricing_totals(instances, updated_at=updated_at, reference_time=reference_time)

    def _write_pricing_totals(
        self,
        instances: dict[str, dict[str, object]],
        reference_time: float | None = None,
    ) -> dict[str, object]:
        billing_now = timestamp_value(reference_time, time.time())
        snapshot = self._build_pricing_totals(instances, updated_at=billing_now, reference_time=billing_now)
        path = self._pricing_totals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return snapshot

    def _seed_legacy_pricing_instance(
        self,
        instance: dict[str, object],
        reference_time: float,
    ) -> bool:
        anchor = clamp_timestamp(instance.get("created_at") or 0, reference_time) or reference_time
        if anchor <= 0:
            return False
        changed = False
        seed_end = min(reference_time, anchor + LEGACY_BILLING_SEED_SECONDS)
        if seed_end < anchor:
            seed_end = anchor
        resource_rate = hourly_price_decimal(
            (instance.get("ipv4_price_hour_eur") or 0) + (instance.get("storage_price_hour_eur") or 0)
        )
        if resource_rate > 0 and not instance.get("resource_windows"):
            instance["resource_windows"] = [{"started_at": anchor, "ended_at": seed_end}]
            changed = True
        if hourly_price_decimal(instance.get("base_price_hour_eur") or 0) > 0 and not instance.get("compute_windows"):
            instance["compute_windows"] = [{"started_at": anchor, "ended_at": seed_end}]
            changed = True
        if changed:
            instance["legacy_estimate"] = True
        return changed

    def _sync_pricing_instance(
        self,
        instances: dict[str, dict[str, object]],
        server: dict[str, object],
        reference_time: float,
    ) -> bool:
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            return False
        previous_raw = instances.get(server_id) if isinstance(instances.get(server_id), dict) else {}
        current = self._normalize_pricing_instance(server_id, previous_raw or {}, reference_time=reference_time)
        before = json.dumps(current, ensure_ascii=True, sort_keys=True)
        current["name"] = str(server.get("name") or current.get("name") or server_id)
        current["zone"] = str(server.get("zone") or current.get("zone") or "")
        current["commercial_type"] = str(server.get("commercial_type") or current.get("commercial_type") or "")
        current["storage_kind"] = str(server.get("storage_kind") or current.get("storage_kind") or "-")
        current["storage_size_label"] = str(server.get("storage_size_label") or current.get("storage_size_label") or "-")
        try:
            current["storage_size_gb"] = float(server.get("storage_size_gb") or current.get("storage_size_gb") or 0)
        except (TypeError, ValueError):
            current["storage_size_gb"] = float(current.get("storage_size_gb") or 0)
        created_at = timestamp_value(server.get("created_at"), float(current.get("created_at") or 0))
        if created_at > 0:
            current["created_at"] = created_at
        state = normalize_state(server.get("state") or current.get("current_state") or "")
        if state:
            current["current_state"] = state
        pricing_known = state not in {"deleted", "error"} or bool(str(server.get("commercial_type") or "").strip())
        if pricing_known:
            base_price = hourly_price_decimal(server.get("base_price_hour_eur") or 0)
            ipv4_price = hourly_price_decimal(server.get("ipv4_price_hour_eur") or 0)
            storage_price = hourly_price_decimal(server.get("storage_price_hour_eur") or 0)
            total_price = hourly_price_decimal(base_price + ipv4_price + storage_price)
            current["base_price_hour_eur"] = float(base_price)
            current["ipv4_price_hour_eur"] = float(ipv4_price)
            current["storage_price_hour_eur"] = float(storage_price)
            current["total_price_hour_eur"] = float(total_price)
        if state != "error":
            anchor = clamp_timestamp(current.get("created_at") or 0, reference_time) or reference_time
            resource_windows = normalize_billing_windows(current.get("resource_windows"), reference_time)
            compute_windows = normalize_billing_windows(current.get("compute_windows"), reference_time)
            resource_active = is_resource_billed_state(state)
            compute_active = is_compute_billed_state(state)
            if current.get("legacy_estimate"):
                if resource_active:
                    resource_windows = [{"started_at": anchor, "ended_at": None}]
                if compute_active:
                    compute_windows = [{"started_at": anchor, "ended_at": None}]
                current["legacy_estimate"] = False
            if resource_active:
                if not resource_windows:
                    resource_windows = [{"started_at": anchor, "ended_at": None}]
                elif not has_open_billing_window(resource_windows):
                    open_billing_window(resource_windows, reference_time)
                current["deleted_at"] = 0.0
            else:
                close_billing_window(resource_windows, reference_time, reference_time)
                current["deleted_at"] = reference_time
            if compute_active:
                if not compute_windows:
                    compute_windows = [{"started_at": anchor, "ended_at": None}]
                elif not has_open_billing_window(compute_windows):
                    open_billing_window(compute_windows, reference_time)
            else:
                close_billing_window(compute_windows, reference_time, reference_time)
            current["resource_windows"] = resource_windows
            current["compute_windows"] = compute_windows
        current["updated_at"] = reference_time
        instances[server_id] = self._normalize_pricing_instance(server_id, current, reference_time=reference_time)
        after = json.dumps(instances[server_id], ensure_ascii=True, sort_keys=True)
        return after != before

    def record_pricing_snapshot(self, server: dict[str, object]) -> dict[str, object]:
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            return self._load_pricing_totals()
        billing_now = timestamp_value(server.get("created_at"), time.time())
        totals = self._load_pricing_totals(reference_time=billing_now)
        instances = dict(totals.get("instances") or {})
        self._sync_pricing_instance(instances, server, billing_now)
        return self._write_pricing_totals(instances, reference_time=billing_now)

    def update_pricing_state(
        self,
        server_id: str,
        state: str,
        changed_at: float | None = None,
    ) -> dict[str, object]:
        normalized_id = str(server_id or "").strip()
        if not normalized_id:
            return self._load_pricing_totals()
        billing_now = timestamp_value(changed_at, time.time())
        totals = self._load_pricing_totals(reference_time=billing_now)
        instances = dict(totals.get("instances") or {})
        if normalized_id not in instances:
            return totals
        current = self._normalize_pricing_instance(normalized_id, instances[normalized_id], reference_time=billing_now)
        current["current_state"] = normalize_state(state)
        resource_windows = normalize_billing_windows(current.get("resource_windows"), billing_now)
        compute_windows = normalize_billing_windows(current.get("compute_windows"), billing_now)
        if is_resource_billed_state(state):
            if not resource_windows:
                anchor = clamp_timestamp(current.get("created_at") or 0, billing_now) or billing_now
                resource_windows = [{"started_at": anchor, "ended_at": None}]
            else:
                open_billing_window(resource_windows, billing_now)
            current["deleted_at"] = 0.0
        else:
            close_billing_window(resource_windows, billing_now, billing_now)
            current["deleted_at"] = billing_now
        if is_compute_billed_state(state):
            if not compute_windows:
                anchor = clamp_timestamp(current.get("created_at") or 0, billing_now) or billing_now
                compute_windows = [{"started_at": anchor, "ended_at": None}]
            else:
                open_billing_window(compute_windows, billing_now)
        else:
            close_billing_window(compute_windows, billing_now, billing_now)
        current["resource_windows"] = resource_windows
        current["compute_windows"] = compute_windows
        current["legacy_estimate"] = False
        current["updated_at"] = billing_now
        instances[normalized_id] = self._normalize_pricing_instance(normalized_id, current, reference_time=billing_now)
        return self._write_pricing_totals(instances, reference_time=billing_now)

    def finalize_pricing_snapshot(
        self,
        server_id: str,
        deleted_at: float | None = None,
    ) -> dict[str, object]:
        normalized_id = str(server_id or "").strip()
        if not normalized_id:
            return self._load_pricing_totals()
        billing_now = timestamp_value(deleted_at, time.time())
        totals = self._load_pricing_totals(reference_time=billing_now)
        instances = dict(totals.get("instances") or {})
        if normalized_id not in instances:
            return totals
        current = self._normalize_pricing_instance(normalized_id, instances[normalized_id], reference_time=billing_now)
        current["seed_billed_cost_eur"] = float(hourly_price_decimal(current.get("billed_cost_eur") or 0))
        current["current_state"] = "deleted"
        current["deleted_at"] = billing_now
        current["legacy_estimate"] = False
        current["updated_at"] = billing_now
        current["resource_windows"] = []
        current["compute_windows"] = []
        instances[normalized_id] = self._normalize_pricing_instance(normalized_id, current, reference_time=billing_now)
        return self._write_pricing_totals(instances, reference_time=billing_now)

    def backfill_pricing_snapshots(
        self,
        servers: list[dict[str, object]],
        reference_time: float | None = None,
    ) -> dict[str, object]:
        billing_now = timestamp_value(reference_time, time.time())
        totals = self._load_pricing_totals(reference_time=billing_now)
        instances = dict(totals.get("instances") or {})
        changed = False
        for server_id, item in list(instances.items()):
            normalized = self._normalize_pricing_instance(server_id, item, reference_time=billing_now)
            if normalized.get("legacy_estimate") and self._seed_legacy_pricing_instance(normalized, billing_now):
                changed = True
            instances[server_id] = self._normalize_pricing_instance(server_id, normalized, reference_time=billing_now)
        for server in servers:
            if not isinstance(server, dict):
                continue
            if not server.get("managed"):
                continue
            if self._sync_pricing_instance(instances, server, billing_now):
                changed = True
        if changed:
            return self._write_pricing_totals(instances, reference_time=billing_now)
        return self._build_pricing_totals(
            instances,
            updated_at=float(totals.get("updated_at") or 0),
            reference_time=billing_now,
        )

    def pricing_summary(self, servers: list[dict[str, object]]) -> dict[str, object]:
        billing_now = time.time()
        current_total = hourly_price_decimal(0)
        for server in servers:
            if not isinstance(server, dict):
                continue
            if "current_rate_hour_eur" not in server:
                server.update(
                    current_rate_details(
                        server.get("state"),
                        server.get("base_price_hour_eur") or 0,
                        server.get("ipv4_price_hour_eur") or 0,
                        server.get("storage_price_hour_eur") or 0,
                    )
                )
            current_total += hourly_price_decimal(server.get("current_rate_hour_eur") or 0)
        current_total = hourly_price_decimal(current_total)
        persisted = self.backfill_pricing_snapshots(servers, reference_time=billing_now)
        return {
            "current_total_rate_hour_eur": float(current_total),
            "current_total_rate_hour": format_hourly_price(current_total),
            "current_total_price_hour_eur": float(current_total),
            "current_total_price_hour": format_hourly_price(current_total),
            "overall_billed_cost_eur": float(persisted["overall_billed_cost_eur"]),
            "overall_billed_cost": str(persisted["overall_billed_cost"]),
            "overall_total_price_hour_eur": float(persisted["overall_billed_cost_eur"]),
            "overall_total_price_hour": str(persisted["overall_billed_cost"]),
            "persisted_instance_count": int(persisted["instance_count"]),
        }

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
            "pricing": self.pricing_summary(servers),
        }

    def create_payload(self) -> dict[str, object]:
        server = self.create_server()
        rows = self.managed_rows()
        servers = self.visible_servers(rows)
        return {
            "server": server,
            "count": len(servers),
            "managed_count": len(rows),
            "max_servers": self.config.server_limit,
            "pricing": self.pricing_summary(servers),
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
        self.finalize_pricing_snapshot(server_id, deleted_at=time.time())
        self.remove_managed_server(server_id)
        rows = self.managed_rows()
        servers = self.visible_servers(rows)
        return {
            "deleted": server_id,
            "deleted_volumes": deleted_volumes,
            "count": len(servers),
            "managed_count": len(rows),
            "max_servers": self.config.server_limit,
            "pricing": self.pricing_summary(servers),
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
        cloud_init = self.render_cloud_init(payload["commercial_type"]).encode("utf-8")
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
        summary = self.server_summary(
            {
                "server_id": server_id,
                "zone": payload["zone"],
                "name": payload["name"],
                "project_id": self.config.default_project_id,
                "created_at": time.time(),
            },
            managed=True,
        )
        self.record_pricing_snapshot(summary)
        return summary

    def render_cloud_init(self, commercial_type: str | None = None) -> str:
        bootstrap_token = self.config.satellite_bootstrap_token or self.config.satellite_api_key
        if not bootstrap_token:
            abort(500, "Satellite bootstrap token is not configured")
        host = self.bootstrap_host()
        designed_bandwidth = (
            server_type_bandwidth_mbps(commercial_type) if commercial_type else 0.0
        )
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
        text = re.sub(
            r'^(\s*SATELLITE_DESIGNED_BANDWIDTH_MBPS=)"[^"]*"$',
            lambda match: f'{match.group(1)}"{designed_bandwidth:g}"',
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
        catalog_entry = server_type_entry(commercial_type)
        image = self.config.default_image
        root_volume_type = self._shorten(
            data.get("root_volume_type")
            or self.config.default_root_volume_type,
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
        if catalog_entry is None or commercial_type not in self.server_type_names():
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
                managed_row = managed_by_id.get(server_id)
                if managed_row is not None:
                    visible_by_id[server_id] = self.server_summary(managed_row, managed=True)
                else:
                    visible_by_id[server_id] = self.server_summary_from_remote(
                        server,
                        zone,
                        managed=False,
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
        summary.update(server_price_details("", ""))
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
        summary["created_at"] = float(server.get("creation_date_ts") or summary["created_at"])
        summary["name"] = self._shorten(server.get("name") or summary["name"], 120) or summary["name"]
        summary["state"] = self._shorten(server.get("state") or "unknown", 40) or "unknown"
        summary["public_ip"] = self.public_ip(server)
        summary["commercial_type"] = self._shorten(server.get("commercial_type") or "", 40) or ""
        summary["bandwidth_mbps"] = server_type_bandwidth_mbps(summary["commercial_type"])
        summary["allowed_actions"] = (
            server.get("allowed_actions")
            if isinstance(server.get("allowed_actions"), list)
            else []
        )
        summary.update(
            server_price_details(
                summary["commercial_type"],
                summary["public_ip"],
                server.get("volumes"),
                fallback_volume_type=self.config.default_root_volume_type if managed else "",
                fallback_size_gb=self.config.default_root_volume_size_gb if managed else None,
            )
        )
        summary.update(
            current_rate_details(
                summary["state"],
                summary["base_price_hour_eur"],
                summary["ipv4_price_hour_eur"],
                summary["storage_price_hour_eur"],
            )
        )
        return summary

    def server_summary_from_remote(
        self,
        server: dict[str, object],
        zone: str,
        managed: bool = False,
    ) -> dict[str, object]:
        commercial_type = self._shorten(server.get("commercial_type") or "", 40) or ""
        summary = {
            "id": str(server.get("id") or ""),
            "zone": zone,
            "name": self._shorten(server.get("name") or "", 120) or str(server.get("id") or ""),
            "project_id": str(server.get("project") or server.get("project_id") or ""),
            "created_at": float(server.get("creation_date_ts") or 0),
            "state": self._shorten(server.get("state") or "unknown", 40) or "unknown",
            "public_ip": self.public_ip(server),
            "error": "",
            "managed": managed,
            "commercial_type": commercial_type,
            "bandwidth_mbps": server_type_bandwidth_mbps(commercial_type),
            "allowed_actions": (
                server.get("allowed_actions")
                if isinstance(server.get("allowed_actions"), list)
                else []
            ),
        }
        summary.update(
            server_price_details(
                commercial_type,
                summary["public_ip"],
                server.get("volumes"),
                fallback_volume_type=self.config.default_root_volume_type if managed else "",
                fallback_size_gb=self.config.default_root_volume_size_gb if managed else None,
            )
        )
        summary.update(
            current_rate_details(
                summary["state"],
                summary["base_price_hour_eur"],
                summary["ipv4_price_hour_eur"],
                summary["storage_price_hour_eur"],
            )
        )
        return summary

    def server_type_names(self) -> set[str]:
        return {entry["name"] for entry in local_storage_server_type_catalog()}

    def available_server_types(self) -> list[dict[str, object]]:
        server_types: list[dict[str, object]] = []
        for entry in local_storage_server_type_catalog():
            pricing = server_price_details(
                str(entry["name"]),
                public_ip="0.0.0.0",
                fallback_volume_type="l_ssd",
                fallback_size_gb=self.config.default_root_volume_size_gb,
            )
            server_types.append(
                {
                    "name": entry["name"],
                    "price_hour": format_hourly_price(entry["price_hour_eur"]),
                    "price_hour_eur": float(hourly_price_decimal(entry["price_hour_eur"])),
                    "ipv4_price_hour": pricing["ipv4_price_hour"],
                    "ipv4_price_hour_eur": pricing["ipv4_price_hour_eur"],
                    "storage_price_hour": pricing["storage_price_hour"],
                    "storage_price_hour_eur": pricing["storage_price_hour_eur"],
                    "total_price_hour": pricing["total_price_hour"],
                    "total_price_hour_eur": pricing["total_price_hour_eur"],
                    "storage_size_label": pricing["storage_size_label"],
                    "vcpus": entry["vcpus"],
                    "memory": entry["memory"],
                    "bandwidth": entry["bandwidth"],
                }
            )
        return server_types

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
