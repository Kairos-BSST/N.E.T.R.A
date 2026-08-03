"""
test_api.py
-------------
Tests the REST API endpoints using FastAPI's TestClient, with the Google
Drive API fully mocked out. This proves the endpoints work correctly
before you ever connect a real Google account.

Run with: python -m pytest tests/test_api.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        import api as api_module
        importlib.reload(api_module)

        from fastapi.testclient import TestClient
        cls.api_module = api_module
        cls.client = TestClient(api_module.app)

    def setUp(self):
        # Fresh state file per test so nothing leaks between tests or
        # between test runs.
        self.api_module.state.state_file_path = tempfile.mktemp(suffix=".json")
        self.api_module.state._state = {}

    def test_serves_frontend(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_auth_status_not_connected_by_default(self):
        resp = self.client.get("/auth/google/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"connected": False})

    def test_login_redirects_to_google(self):
        with patch.object(self.api_module.drive_auth, "get_authorization_url",
                           return_value=("https://accounts.google.com/fake", "state123")):
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

        # Step 1: simulate the OAuth callback succeeding
        with patch.object(self.api_module.drive_auth, "exchange_code_for_credentials", return_value=fake_creds):
            resp = self.client.get(
                "/auth/google/callback",
                params={"code": "fakecode", "state": "fakestate"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 307)
        session_cookie = resp.cookies.get("netra_session")
        self.assertIsNotNone(session_cookie)
        self.client.cookies.set("netra_session", session_cookie)

        # Step 2: list files using the now-connected session
        with patch.object(self.api_module.drive_auth, "get_credentials", return_value=fake_creds), \
             patch.object(self.api_module, "DriveClient") as MockDriveClient:
            instance = MockDriveClient.return_value
            instance.list_video_files.return_value = [
                {"id": "abc123", "name": "cam1.mp4", "size": "1048576", "mimeType": "video/mp4"}
            ]
            resp2 = self.client.get("/drive/files")
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.json()["files"][0]["name"], "cam1.mp4")

            # Step 3: fetch the file
            instance.download_file.return_value = 1048576
            resp3 = self.client.post("/drive/fetch", json={"file_id": "abc123", "file_name": "cam1.mp4"})
            self.assertEqual(resp3.status_code, 200)
            body = resp3.json()
            self.assertEqual(body["status"], "fetched")
            self.assertEqual(body["size_bytes"], 1048576)

        # Step 4: confirm it shows up in /videos
        resp4 = self.client.get("/videos")
        self.assertIn("abc123", resp4.json())

    def test_fetch_requires_connection(self):
        resp = self.client.post("/drive/fetch", json={"file_id": "x", "file_name": "x.mp4"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()