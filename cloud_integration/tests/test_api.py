"""
test_api.py
-------------
Tests the REST API endpoints using FastAPI's TestClient, with GCS fully
mocked out. This proves the endpoints work correctly before you ever
point them at a real bucket.

Run with: python -m pytest tests/test_api.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch GCS before importing api.py, since api.py builds a
        # VideoFetcher (and therefore a GCSClient) at import time.
        cls.patcher_validate = patch("gcs_client.Config.validate", return_value=None)
        cls.patcher_storage = patch("gcs_client.storage.Client")
        cls.patcher_validate.start()
        cls.mock_storage_client = cls.patcher_storage.start()

        import importlib
        import cloud_integration.api as api_module
        importlib.reload(api_module)  # ensure it picks up the patched Client

        from fastapi.testclient import TestClient
        cls.api_module = api_module
        cls.client = TestClient(api_module.app)

    def setUp(self):
        # Redirect state to a fresh temp file for every test so leftover
        # state from a previous test run (or a real run of the API) can
        # never leak in and make these tests flaky.
        import tempfile
        self.api_module._fetcher.state.state_file_path = tempfile.mktemp(suffix=".json")
        self.api_module._fetcher.state._state = {}
        self.api_module._last_fetch_new_videos.clear()

    @classmethod
    def tearDownClass(cls):
        cls.patcher_validate.stop()
        cls.patcher_storage.stop()

    def test_health_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_fetch_returns_new_videos(self):
        blob = MagicMock()
        blob.name = "camera1/clip.mp4"

        with patch.object(self.api_module._fetcher.gcs, "list_new_videos", return_value=[blob]), \
             patch.object(self.api_module._fetcher.gcs, "download_video", return_value=999):
            resp = self.client.post("/fetch")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["new_videos_count"], 1)
        self.assertEqual(body["new_videos"][0]["blob_name"], "camera1/clip.mp4")

    def test_videos_list_reflects_state(self):
        self.api_module._fetcher.state.mark_fetched("camera1/x.mp4", "/tmp/x.mp4", 10)
        resp = self.client.get("/videos")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("camera1/x.mp4", resp.json())

    def test_signed_url_endpoint(self):
        with patch.object(self.api_module._fetcher.gcs, "generate_signed_url",
                           return_value="https://storage.googleapis.com/fake-signed-url"):
            resp = self.client.get("/videos/signed-url", params={"blob_name": "camera1/clip.mp4"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["blob_name"], "camera1/clip.mp4")
        self.assertTrue(body["signed_url"].startswith("https://"))

    def test_upload_endpoint(self):
        with patch.object(self.api_module._fetcher.gcs, "upload_file") as mock_upload:
            resp = self.client.post("/upload", json={
                "local_path": "/tmp/fake.mp4",
                "destination_blob_name": "processed/fake.mp4",
            })

        self.assertEqual(resp.status_code, 200)
        mock_upload.assert_called_once_with("/tmp/fake.mp4", "processed/fake.mp4")


if __name__ == "__main__":
    unittest.main()