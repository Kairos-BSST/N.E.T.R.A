"""
test_drive_auth.py
---------------------
Tests the OAuth state/session bookkeeping in drive_auth.py, with the
actual Google OAuth flow mocked out. No real network or Google account
needed.

Run with: python -m pytest tests/test_drive_auth.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import drive_auth


class TestDriveAuth(unittest.TestCase):
    def setUp(self):
        # Clear global state between tests so they don't interfere with each other
        drive_auth.SESSION_STORE.clear()
        drive_auth._PENDING_STATES.clear()

    def test_get_authorization_url_registers_state(self):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/fake", "state123")

        with patch("drive_auth.build_flow", return_value=mock_flow):
            auth_url, state = drive_auth.get_authorization_url()

        self.assertEqual(auth_url, "https://accounts.google.com/fake")
        self.assertIn(state, drive_auth._PENDING_STATES)

    def test_exchange_code_rejects_unknown_state(self):
        with self.assertRaises(ValueError):
            drive_auth.exchange_code_for_credentials("some-code", "never-issued-state")

    def test_exchange_code_succeeds_with_known_state(self):
        drive_auth._PENDING_STATES["good-state"] = True
        mock_flow = MagicMock()
        mock_flow.credentials = MagicMock(name="fake_credentials")

        with patch("drive_auth.build_flow", return_value=mock_flow):
            creds = drive_auth.exchange_code_for_credentials("some-code", "good-state")

        self.assertEqual(creds, mock_flow.credentials)
        # State should be consumed - can't be reused
        self.assertNotIn("good-state", drive_auth._PENDING_STATES)

    def test_store_and_get_credentials(self):
        fake_creds = MagicMock()
        fake_creds.expired = False
        session_id = drive_auth.new_session_id()

        drive_auth.store_credentials(session_id, fake_creds)
        retrieved = drive_auth.get_credentials(session_id)

        self.assertEqual(retrieved, fake_creds)

    def test_get_credentials_returns_none_for_unknown_session(self):
        self.assertIsNone(drive_auth.get_credentials("nonexistent-session"))
        self.assertIsNone(drive_auth.get_credentials(None))

    def test_expired_credentials_are_refreshed(self):
        fake_creds = MagicMock()
        fake_creds.expired = True
        fake_creds.refresh_token = "refresh-me"
        session_id = drive_auth.new_session_id()
        drive_auth.store_credentials(session_id, fake_creds)

        with patch("google.auth.transport.requests.Request"):
            retrieved = drive_auth.get_credentials(session_id)

        fake_creds.refresh.assert_called_once()
        self.assertEqual(retrieved, fake_creds)

    def test_is_connected(self):
        session_id = drive_auth.new_session_id()
        self.assertFalse(drive_auth.is_connected(session_id))

        fake_creds = MagicMock()
        fake_creds.expired = False
        drive_auth.store_credentials(session_id, fake_creds)
        self.assertTrue(drive_auth.is_connected(session_id))


if __name__ == "__main__":
    unittest.main()