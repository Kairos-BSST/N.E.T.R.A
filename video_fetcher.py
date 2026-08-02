"""
video_fetcher.py
-----------------
The core service. On a fixed interval it:

    1. Lists all video objects currently in the GCS bucket
    2. Skips any it has already downloaded (via StateTracker)
    3. Downloads the new ones to LOCAL_DOWNLOAD_DIR
    4. Calls an on_new_video callback for each one, so the rest of your
       N.E.T.R.A pipeline (AI analytics, event detection, dashboard ingest)
       can pick it up immediately.

This is intentionally provider-agnostic at the callback boundary: your
analytics module doesn't need to know anything about GCS. It just receives
a local file path and the original blob name once a video is ready.
"""

import logging
import os
import time
from typing import Callable, Optional

from config import Config
from gcs_client import GCSClient
from state_tracker import StateTracker

logger = logging.getLogger("netra.video_fetcher")

# Signature: on_new_video(local_path: str, blob_name: str) -> None
OnNewVideoCallback = Callable[[str, str], None]


class VideoFetcher:
    def __init__(self, on_new_video: Optional[OnNewVideoCallback] = None):
        Config.validate()
        self.gcs = GCSClient()
        self.state = StateTracker(Config.STATE_FILE_PATH)
        self.on_new_video = on_new_video

    def _local_path_for(self, blob_name: str) -> str:
        # Preserve folder structure (e.g. camera1/2026-08-01_1200.mp4) locally
        # so operators can trace files back to their source easily.
        return os.path.join(Config.LOCAL_DOWNLOAD_DIR, blob_name)

    def fetch_once(self) -> int:
        """
        Do a single pass: check the bucket, download anything new.
        Returns the number of newly fetched videos.
        """
        videos = self.gcs.list_new_videos()
        new_count = 0

        for blob in videos:
            if self.state.has_been_fetched(blob.name):
                continue

            local_path = self._local_path_for(blob.name)
            try:
                size = self.gcs.download_video(blob, local_path)
            except Exception:
                # Already logged inside download_video; skip and try again next cycle
                continue

            self.state.mark_fetched(blob.name, local_path, size)
            new_count += 1

            if self.on_new_video:
                try:
                    self.on_new_video(local_path, blob.name)
                except Exception as e:
                    logger.exception("on_new_video callback failed for %s: %s", blob.name, e)

        if new_count:
            logger.info("Fetch cycle complete: %d new video(s) downloaded.", new_count)
        else:
            logger.debug("Fetch cycle complete: no new videos.")

        return new_count

    def run_forever(self) -> None:
        """Poll the bucket indefinitely at POLL_INTERVAL_SECONDS."""
        logger.info(
            "Starting continuous fetch loop (interval=%ds, bucket=%s, prefix='%s')",
            Config.POLL_INTERVAL_SECONDS, Config.GCS_BUCKET_NAME, Config.GCS_VIDEO_PREFIX,
        )
        while True:
            try:
                self.fetch_once()
            except Exception as e:
                # Never let a single bad cycle kill the whole service
                logger.exception("Unexpected error during fetch cycle: %s", e)
            time.sleep(Config.POLL_INTERVAL_SECONDS)