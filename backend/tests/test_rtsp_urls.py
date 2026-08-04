"""Unit tests for RTSP URL builders and live connect payload shaping."""

import unittest

from video_sources.rtsp import build_rtsp_url, validate_rtsp_url
from video_sources.base import InvalidStreamUrlError


class TestRtspBuilders(unittest.TestCase):
    def test_hikvision(self):
        url = build_rtsp_url(
            "hikvision",
            ip="192.168.1.64",
            port=554,
            username="admin",
            password="pass/word",
        )
        self.assertEqual(
            url,
            "rtsp://admin:pass%2Fword@192.168.1.64:554/Streaming/Channels/101",
        )

    def test_dahua(self):
        url = build_rtsp_url(
            "dahua",
            ip="10.0.0.5",
            username="user",
            password="pwd",
        )
        self.assertEqual(
            url,
            "rtsp://user:pwd@10.0.0.5:554/cam/realmonitor?channel=1&subtype=0",
        )

    def test_cp_plus_matches_dahua(self):
        dahua = build_rtsp_url("dahua", ip="1.2.3.4", username="a", password="b")
        cp = build_rtsp_url("cp_plus", ip="1.2.3.4", username="a", password="b")
        self.assertEqual(dahua, cp)

    def test_missing_ip(self):
        with self.assertRaises(InvalidStreamUrlError):
            build_rtsp_url("hikvision", ip="")

    def test_validate_rtsp(self):
        self.assertTrue(validate_rtsp_url("rtsp://x/y").startswith("rtsp://"))
        with self.assertRaises(InvalidStreamUrlError):
            validate_rtsp_url("http://not-rtsp")


if __name__ == "__main__":
    unittest.main()
