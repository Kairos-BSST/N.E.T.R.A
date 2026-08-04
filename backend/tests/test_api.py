"""
test_api.py
-------------
Tests the REST API endpoints using FastAPI's TestClient, with the Google
Drive API fully mocked out.

Run from backend/:
    python -m pytest tests/test_api.py -v
"""

import os
import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        import deps
        import inputs.analysis as analysis_routes
        import inputs.drive as drive_routes
        import inputs.live as live_routes
        import inputs.upload as upload_routes
        import api as api_module

        importlib.reload(deps)
        importlib.reload(analysis_routes)
        importlib.reload(drive_routes)
        importlib.reload(live_routes)
        importlib.reload(upload_routes)
        importlib.reload(api_module)

        from fastapi.testclient import TestClient

        cls.api_module = api_module
        cls.deps = deps
        cls.drive_routes = drive_routes
        cls.client = TestClient(api_module.app)

    def setUp(self):
        self.deps.state.state_file_path = tempfile.mktemp(suffix=".json")
        self.deps.state._state = {}

    def test_serves_frontend(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_auth_status_not_connected_by_default(self):
        resp = self.client.get("/auth/google/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"connected": False})

    def test_login_redirects_to_google(self):
        with patch.object(
            self.drive_routes.drive_auth,
            "get_authorization_url",
            return_value=("https://accounts.google.com/fake", "state123"),
        ):
            resp = self.client.get("/auth/google/login", follow_redirects=False)

        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["location"], "https://accounts.google.com/fake")
        self.assertEqual(resp.cookies.get("oauth_state"), "state123")

    def test_drive_files_requires_connection(self):
        resp = self.client.get("/drive/files")
        self.assertEqual(resp.status_code, 401)

    def test_full_oauth_and_fetch_flow(self):
        fake_creds = MagicMock()
        fake_creds.expired = False

        with patch.object(
            self.drive_routes.drive_auth,
            "exchange_code_for_credentials",
            return_value=fake_creds,
        ):
            resp = self.client.get(
                "/auth/google/callback",
                params={"code": "fakecode", "state": "fakestate"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 307)
        session_cookie = resp.cookies.get("netra_session")
        self.assertIsNotNone(session_cookie)
        self.client.cookies.set("netra_session", session_cookie)

        with patch.object(self.drive_routes.drive_auth, "get_credentials", return_value=fake_creds), \
             patch.object(self.drive_routes, "DriveClient") as MockDriveClient:
            instance = MockDriveClient.return_value
            instance.list_video_files.return_value = [
                {"id": "abc123", "name": "cam1.mp4", "size": "1048576", "mimeType": "video/mp4"}
            ]
            resp2 = self.client.get("/drive/files")
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.json()["files"][0]["name"], "cam1.mp4")

            instance.download_file.return_value = 1048576
            resp3 = self.client.post("/drive/fetch", json={"file_id": "abc123", "file_name": "cam1.mp4"})
            self.assertEqual(resp3.status_code, 200)
            body = resp3.json()
            self.assertEqual(body["status"], "fetched")
            self.assertEqual(body["size_bytes"], 1048576)
            self.assertIn("analysis", body)

        resp4 = self.client.get("/videos")
        self.assertIn("abc123", resp4.json())

    def test_fetch_requires_connection(self):
        resp = self.client.post("/drive/fetch", json={"file_id": "x", "file_name": "x.mp4"})
        self.assertEqual(resp.status_code, 401)

    def test_upload_video_and_queue_analysis(self):
        upload_dir = tempfile.mkdtemp()
        from config import Config

        Config.LOCAL_UPLOAD_DIR = upload_dir

        payload = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
        resp = self.client.post(
            "/upload",
            files={"file": ("clip.mp4", BytesIO(payload), "video/mp4")},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "uploaded")
        self.assertEqual(body["size_bytes"], len(payload))
        self.assertEqual(body["analysis"]["source"], "upload")
        self.assertEqual(body["analysis"]["status"], "queued_placeholder")
        self.assertTrue(os.path.isfile(body["local_path"]))

    def test_upload_rejects_non_video(self):
        resp = self.client.post(
            "/upload",
            files={"file": ("notes.txt", BytesIO(b"hello"), "text/plain")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_live_analysis_placeholder(self):
        resp = self.client.post("/analysis/live", json={"stream_url": "rtsp://cam/stream1"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["analysis"]["source"], "live")
        self.assertEqual(body["analysis"]["status"], "queued_placeholder")


if __name__ == "__main__":
    unittest.main()
