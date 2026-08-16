import io
import logging
import os

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials

logger = logging.getLogger("netra.drive_client")

VIDEO_MIME_QUERY = "mimeType contains 'video/' and trashed = false"

class DriveClient:
    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def list_video_files(self, page_size: int = 25) -> list:
        """Return a list of {id, name, size, mimeType, modifiedTime} for video files in the user's Drive."""
        results = self.service.files().list(
            q=VIDEO_MIME_QUERY,
            pageSize=page_size,
            fields="files(id, name, size, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute()
        return results.get("files", [])

    def download_file(self, file_id: str, destination_path: str) -> int:
        """
        Download a Drive file's content to destination_path, streaming in
        chunks. Returns the number of bytes written.
        """
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        request = self.service.files().get_media(fileId=file_id)

        tmp_path = f"{destination_path}.part"
        with io.FileIO(tmp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info(
                        "Drive download progress for %s: %d%%",
                        file_id, int(status.progress() * 100),
                    )

        os.replace(tmp_path, destination_path)
        size = os.path.getsize(destination_path)
        logger.info("Downloaded Drive file %s -> %s (%d bytes)", file_id, destination_path, size)
        return size

    def get_file_metadata(self, file_id: str) -> dict:
        return self.service.files().get(fileId=file_id, fields="id, name, size, mimeType").execute()