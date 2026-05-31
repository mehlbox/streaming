import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-flask")

import app as streaming_app
import scaleway as scaleway_module


class SatelliteAssignmentTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
