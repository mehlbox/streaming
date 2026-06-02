import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-flask")

import app as streaming_app
import scaleway as scaleway_module


class SatelliteAssignmentTests(unittest.TestCase):
    def make_scaleway_manager(self, totals_path: str) -> scaleway_module.ScalewayManager:
        config = scaleway_module.ScalewayConfig(
            api_base_url="https://api.scaleway.com",
            access_key="access",
            secret_key="secret",
            default_organization_id="org",
            default_project_id="project",
            default_zone="fr-par-1",
            default_commercial_type="DEV1-S",
            default_image="ubuntu_noble",
            default_root_volume_type="l_ssd",
            default_root_volume_size_gb=10,
            server_name_prefix="instance",
            manage_token="manage-token",
            server_limit=10,
            allowed_zones=("fr-par-1",),
            managed_tags=("streaming-satellite",),
            satellite_bootstrap_dir="/tmp",
            streaming_host="example.com",
            public_origin_url="https://example.com",
            satellite_api_key="sat-api-key",
            satellite_bootstrap_token="bootstrap-token",
            cost_totals_path=totals_path,
        )
        return scaleway_module.ScalewayManager(
            config=config,
            connect_db=lambda: sqlite3.connect(":memory:"),
            init_db=lambda: None,
            shorten=lambda value, limit: str(value or "")[:limit],
            normalize_ip_address=lambda value: str(value or "").strip(),
            request_external_base_url=lambda: "https://example.com",
        )

    def test_local_hls_viewer_count_ignores_internal_probe_cookie(self):
        now = 100.0
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("95.0|real-viewer|-|-|0\n")
            handle.write(f"96.0|{streaming_app.HLS_STATUS_PROBE_VIEWER_ID}|-|-|0\n")
            handle.write("97.0|real-viewer|-|-|0\n")
            log_path = handle.name
        self.addCleanup(lambda: os.path.exists(log_path) and os.unlink(log_path))

        with patch.object(streaming_app, "HLS_ACCESS_LOG", log_path), \
             patch.object(streaming_app, "HLS_VIEWER_WINDOW", 15):
            self.assertEqual(streaming_app.local_hls_viewer_count(now), 1)

    def test_weighted_selection_uses_capacity_score(self):
        rows = [
            (10.0, {"url": "https://node1.example.com/hls"}),
            (30.0, {"url": "https://node2.example.com/hls"}),
        ]

        with patch.object(streaming_app.random, "uniform", return_value=25.0):
            selected = streaming_app.select_weighted_satellite(rows)

        self.assertEqual(selected["url"], "https://node2.example.com/hls")

    def test_weighted_selection_ignores_full_satellites(self):
        rows = [
            (0.0, {"url": "https://full.example.com/hls"}),
            (5.0, {"url": "https://ready.example.com/hls"}),
        ]

        with patch.object(streaming_app.random, "uniform", return_value=0.0):
            selected = streaming_app.select_weighted_satellite(rows)

        self.assertEqual(selected["url"], "https://ready.example.com/hls")

    def test_manifest_probe_uses_configured_stream_name(self):
        with patch.object(streaming_app, "STREAM_NAME", "event"):
            url = streaming_app.satellite_manifest_url("https://node1.example.com/hls")

        self.assertEqual(url, "https://node1.example.com/hls/event.m3u8")

    def test_scaleway_server_type_bandwidth_uses_catalog(self):
        self.assertEqual(scaleway_module.server_type_bandwidth_mbps("DEV1-S"), 200.0)

    def test_scaleway_server_type_bandwidth_returns_zero_for_unknown_type(self):
        self.assertEqual(scaleway_module.server_type_bandwidth_mbps("UNKNOWN"), 0.0)

    def test_scaleway_hourly_pricing_includes_ipv4_and_storage(self):
        pricing = scaleway_module.server_price_details(
            "DEV1-S",
            public_ip="51.15.0.1",
            fallback_volume_type="l_ssd",
            fallback_size_gb=10,
        )

        self.assertEqual(pricing["base_price_hour"], "€0.00898/hour")
        self.assertEqual(pricing["ipv4_price_hour"], "€0.005/hour")
        self.assertEqual(pricing["storage_price_hour"], "€0.00049/hour")
        self.assertEqual(pricing["total_price_hour"], "€0.01447/hour")

    def test_scaleway_available_server_types_only_include_local_storage_types(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_scaleway_manager(os.path.join(temp_dir, "scaleway-costs.json"))

            server_types = manager.available_server_types()

        self.assertEqual([item["name"] for item in server_types], [
            "STARDUST1-S",
            "DEV1-S",
            "DEV1-M",
            "DEV1-L",
            "DEV1-XL",
        ])
        self.assertTrue(all("root_volume_type" not in item for item in server_types))
        self.assertTrue(all("storage" not in item for item in server_types))
        self.assertTrue(all("vcpu_type" not in item for item in server_types))

    def test_scaleway_server_type_names_exclude_block_only_types(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_scaleway_manager(os.path.join(temp_dir, "scaleway-costs.json"))

            server_type_names = manager.server_type_names()

        self.assertIn("DEV1-S", server_type_names)
        self.assertNotIn("PLAY2-PICO", server_type_names)

    def test_scaleway_from_env_falls_back_to_local_storage_default_type(self):
        with patch.dict(os.environ, {"SCW_DEFAULT_COMMERCIAL_TYPE": "PLAY2-PICO"}, clear=False):
            config = scaleway_module.ScalewayConfig.from_env(
                streaming_host="example.com",
                public_origin_url="https://example.com",
                satellite_api_key="sat-api-key",
                satellite_bootstrap_token="bootstrap-token",
                state_db_path="/tmp/state.db",
            )

        self.assertEqual(config.default_commercial_type, "STARDUST1-S")

    def test_scaleway_persistent_totals_are_stored_in_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            totals_path = os.path.join(temp_dir, "scaleway-costs.json")
            manager = self.make_scaleway_manager(totals_path)

            manager.record_pricing_snapshot(
                {
                    "id": "srv-1",
                    "name": "instance1",
                    "commercial_type": "DEV1-S",
                    "zone": "fr-par-1",
                    "created_at": 1.0,
                    "storage_kind": "Local Storage",
                    "storage_size_gb": 10.0,
                    "storage_size_label": "10 GB",
                    "base_price_hour_eur": 0.00898,
                    "ipv4_price_hour_eur": 0.005,
                    "storage_price_hour_eur": 0.00049,
                    "total_price_hour_eur": 0.01447,
                }
            )
            manager.record_pricing_snapshot(
                {
                    "id": "srv-2",
                    "name": "instance2",
                    "commercial_type": "DEV1-M",
                    "zone": "fr-par-1",
                    "created_at": 2.0,
                    "storage_kind": "Local Storage",
                    "storage_size_gb": 10.0,
                    "storage_size_label": "10 GB",
                    "base_price_hour_eur": 0.0202,
                    "ipv4_price_hour_eur": 0.005,
                    "storage_price_hour_eur": 0.00049,
                    "total_price_hour_eur": 0.02569,
                }
            )

            totals = manager.pricing_summary([])
            payload = manager._load_pricing_totals()

        self.assertEqual(totals["current_total_price_hour"], "€0/hour")
        self.assertEqual(totals["overall_total_price_hour"], "€0.04016/hour")
        self.assertEqual(totals["persisted_instance_count"], 2)
        self.assertIn("srv-1", payload["instances"])
        self.assertIn("srv-2", payload["instances"])

    def test_scaleway_pricing_summary_backfills_existing_managed_servers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            totals_path = os.path.join(temp_dir, "scaleway-costs.json")
            manager = self.make_scaleway_manager(totals_path)

            totals = manager.pricing_summary(
                [
                    {
                        "id": "srv-existing",
                        "name": "instance-existing",
                        "zone": "fr-par-1",
                        "managed": True,
                        "state": "running",
                        "commercial_type": "DEV1-S",
                        "storage_kind": "Local Storage",
                        "storage_size_gb": 10.0,
                        "storage_size_label": "10 GB",
                        "base_price_hour_eur": 0.00898,
                        "ipv4_price_hour_eur": 0.005,
                        "storage_price_hour_eur": 0.00049,
                        "total_price_hour_eur": 0.01447,
                    }
                ]
            )
            payload = manager._load_pricing_totals()

        self.assertEqual(totals["overall_total_price_hour"], "€0.01447/hour")
        self.assertEqual(totals["persisted_instance_count"], 1)
        self.assertIn("srv-existing", payload["instances"])

    def test_effective_local_viewer_count_prefers_local_satellite_row(self):
        with patch.object(streaming_app, "local_satellite_viewer_count", return_value=(4, True)), \
             patch.object(streaming_app, "local_stream_viewer_count", return_value=(6, True)):
            count, observed = streaming_app.effective_local_viewer_count()

        self.assertEqual(count, 4)
        self.assertTrue(observed)

    def test_effective_local_viewer_count_falls_back_to_stream_count(self):
        with patch.object(streaming_app, "local_satellite_viewer_count", return_value=(0, False)), \
             patch.object(streaming_app, "local_stream_viewer_count", return_value=(6, True)):
            count, observed = streaming_app.effective_local_viewer_count()

        self.assertEqual(count, 6)
        self.assertTrue(observed)

    def test_build_state_snapshot_uses_cluster_total_for_status_total(self):
        with patch.object(streaming_app, "is_live", return_value=True), \
             patch.object(streaming_app, "is_audio_live", return_value=False), \
             patch.object(streaming_app, "total_viewer_count", return_value=9):
            snapshot = streaming_app.build_state_snapshot(local_count=4, local_observed=True)

        self.assertEqual(snapshot["count"], 9)
        self.assertEqual(snapshot["local_count"], 4)
        self.assertNotIn("cluster_count", snapshot)

    def test_build_state_snapshot_uses_cluster_total_without_local_observation(self):
        with patch.object(streaming_app, "is_live", return_value=True), \
             patch.object(streaming_app, "is_audio_live", return_value=False), \
             patch.object(streaming_app, "total_viewer_count", return_value=7):
            snapshot = streaming_app.build_state_snapshot(local_count=5, local_observed=False)

        self.assertEqual(snapshot["count"], 7)
        self.assertEqual(snapshot["local_count"], 0)
        self.assertNotIn("cluster_count", snapshot)

    def test_delete_replaced_satellite_rows_removes_matching_name_or_url(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE satellites(id TEXT, name TEXT, url TEXT)")
        conn.executemany(
            "INSERT INTO satellites(id, name, url) VALUES(?, ?, ?)",
            [
                ("same-name", "node1", "https://old.example.com/hls"),
                ("same-url", "node2", "https://node1.example.com/hls"),
                ("other", "node3", "https://node3.example.com/hls"),
            ],
        )

        deleted = streaming_app.delete_replaced_satellite_rows(
            conn,
            "node1",
            "https://node1.example.com/hls",
        )

        self.assertEqual(deleted, 2)
        self.assertEqual(
            conn.execute("SELECT id FROM satellites ORDER BY id").fetchall(),
            [("other",)],
        )


if __name__ == "__main__":
    unittest.main()
