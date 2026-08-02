"""
test_drive_client.py
------------------------
Tests DriveClient's list/download logic with the actual Google API
client (googleapiclient.discovery.build) mocked out entirely.

Run with: python -m pytest tests/test_drive_client.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestDriveClient(unittest.TestCase):
    def _make_client(self, mock_service):
        with patch("drive_client.build", return_value=mock_service):
            from cloud_integration.drive_client import DriveClient
            return DriveClient(credentials=MagicMock())

    def test_list_video_files_uses_video_mime_filter(self):
        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "1", "name": "clip.mp4", "size": "1000", "mimeType": "video/mp4"}]
        }
        client = self._make_client(mock_service)

        files = client.list_video_files()

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "clip.mp4")
        # Confirm the query filters to video mimeTypes and excludes trashed files
        call_kwargs = mock_service.files().list.call_args_list[-1].kwargs
        self.assertIn("mimeType contains 'video/'", call_kwargs["q"])
        self.assertIn("trashed = false", call_kwargs["q"])

    def test_list_video_files_empty_result(self):
        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {}
        client = self._make_client(mock_service)

        files = client.list_video_files()
        self.assertEqual(files, [])

    def test_download_file_writes_bytes_and_renames(self):
        mock_service = MagicMock()
        client = self._make_client(mock_service)

        tmp_dir = tempfile.mkdtemp()
        dest_path = os.path.join(tmp_dir, "clip.mp4")

        # Simulate MediaIoBaseDownload doing exactly one chunk then finishing
        fake_status = MagicMock()
        fake_status.progress.return_value = 1.0

        def fake_next_chunk():
            return (fake_status, True)

        with patch("drive_client.MediaIoBaseDownload") as MockDownloader:
            mock_downloader_instance = MockDownloader.return_value
            mock_downloader_instance.next_chunk.side_effect = fake_next_chunk

            # The real download writes to a FileIO handle; simulate that by
            # writing bytes ourselves when next_chunk is first called.
            def side_effect_writing(*a, **kw):
                # write some bytes to the .part file so getsize() works after rename
                part_path = dest_path + ".part"
                with open(part_path, "wb") as f:
                    f.write(b"x" * 2048)
                return (fake_status, True)

            mock_downloader_instance.next_chunk.side_effect = side_effect_writing

            size = client.download_file("file-id-123", dest_path)

        self.assertTrue(os.path.exists(dest_path))
        self.assertEqual(size, 2048)

    def test_get_file_metadata(self):
        mock_service = MagicMock()
        mock_service.files().get().execute.return_value = {
            "id": "abc", "name": "clip.mp4", "size": "500", "mimeType": "video/mp4"
        }
        client = self._make_client(mock_service)

        meta = client.get_file_metadata("abc")
        self.assertEqual(meta["name"], "clip.mp4")


if __name__ == "__main__":
    unittest.main()