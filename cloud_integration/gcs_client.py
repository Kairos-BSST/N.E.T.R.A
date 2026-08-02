"""
gcs_client.py
-------------
Thin wrapper around google-cloud-storage that gives N.E.T.R.A a simple,
reliable interface for talking to Google Cloud Storage:

    - list_new_videos()   -> list objects under a prefix, filtered by extension
    - download_video()    -> stream a blob to local disk with retries
    - generate_signed_url() -> give the dashboard a temporary, secure playback link
    - upload_file()       -> for pushing processed clips / evidence back to the cloud

This is the only file that should import google.cloud.storage directly;
everything else in the project talks to this wrapper, so if you ever swap
providers (S3, Azure Blob) only this file needs to change.
"""

import logging
import os
import time
from datetime import timedelta
from typing import List

from google.cloud import storage
from google.api_core.exceptions import GoogleAPIError

from config import Config

logger = logging.getLogger("netra.gcs_client")


class GCSClient:
    def __init__(self):
        Config.validate()
        # google-cloud-storage reads GOOGLE_APPLICATION_CREDENTIALS from the
        # environment automatically, but we set it explicitly to be safe in
        # case the process env differs from the .env file.
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = Config.GOOGLE_APPLICATION_CREDENTIALS
        self.client = storage.Client()
        self.bucket = self.client.bucket(Config.GCS_BUCKET_NAME)
        logger.info("Connected to GCS bucket: %s", Config.GCS_BUCKET_NAME)

    def list_new_videos(self) -> List[storage.Blob]:
        """
        Return every blob under GCS_VIDEO_PREFIX whose extension matches
        ALLOWED_VIDEO_EXTENSIONS. Filtering happens here so callers never
        have to deal with non-video objects (thumbnails, logs, etc.)
        that might live in the same bucket.
        """
        blobs = self.client.list_blobs(self.bucket, prefix=Config.GCS_VIDEO_PREFIX)
        videos = [
            b for b in blobs
            if b.name.lower().endswith(Config.ALLOWED_VIDEO_EXTENSIONS)
        ]
        return videos

    def download_video(self, blob: storage.Blob, local_path: str) -> int:
        """
        Download a single blob to local_path, streaming in chunks so large
        video files don't blow up memory. Retries with exponential backoff
        on transient network/API errors.

        Returns the number of bytes written.
        """
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        tmp_path = f"{local_path}.part"

        attempt = 0
        while True:
            attempt += 1
            try:
                blob.download_to_filename(tmp_path)
                os.replace(tmp_path, local_path)  # atomic rename once complete
                size = os.path.getsize(local_path)
                logger.info("Downloaded %s (%d bytes) -> %s", blob.name, size, local_path)
                return size
            except GoogleAPIError as e:
                if attempt >= Config.MAX_DOWNLOAD_RETRIES:
                    logger.error("Failed to download %s after %d attempts: %s", blob.name, attempt, e)
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise
                backoff = 2 ** attempt
                logger.warning(
                    "Download error for %s (attempt %d/%d): %s. Retrying in %ds...",
                    blob.name, attempt, Config.MAX_DOWNLOAD_RETRIES, e, backoff,
                )
                time.sleep(backoff)

    def generate_signed_url(self, blob_name: str, expiry_minutes: int = 60) -> str:
        """
        Generate a temporary, secure URL the dashboard can use to stream a
        video directly from GCS without exposing the bucket publicly or
        routing large video bytes through your app server.
        """
        blob = self.bucket.blob(blob_name)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
        )
        return url

    def upload_file(self, local_path: str, destination_blob_name: str) -> None:
        """
        Upload a local file (e.g. a processed clip, an alert thumbnail, or
        an evidence package) back up to the bucket.
        """
        blob = self.bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        logger.info("Uploaded %s -> gs://%s/%s", local_path, Config.GCS_BUCKET_NAME, destination_blob_name)