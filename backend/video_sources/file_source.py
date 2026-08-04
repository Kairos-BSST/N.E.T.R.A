"""
FileSource — continuous frames from a local video file (upload path).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import cv2

from video_sources.base import InvalidStreamUrlError, VideoSource, VideoSourceError

logger = logging.getLogger("netra.video.file")


class FileSource(VideoSource):
    """Reads frames from a video file on disk using the shared VideoSource API."""

    source_kind = "file"

    def __init__(self, path: str, *, loop: bool = False):
        self._path = path
        self._loop = loop
        self._cap: Optional[cv2.VideoCapture] = None

    @property
    def label(self) -> str:
        return os.path.basename(self._path) or self._path

    @property
    def path(self) -> str:
        return self._path

    def connect(self) -> None:
        self.release()
        if not self._path or not os.path.isfile(self._path):
            raise InvalidStreamUrlError(f"Video file not found: {self._path}")

        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            cap.release()
            raise VideoSourceError(
                f"Could not open video file: {self._path}",
                code="file_open_failed",
            )

        self._cap = cap
        logger.info("FileSource opened: %s", self._path)

    def read(self):
        if self._cap is None or not self._cap.isOpened():
            return False, None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            if self._loop and self._cap is not None:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    return True, frame
            return False, None
        return True, frame

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.exception("Error releasing FileSource")
            self._cap = None

    def isOpened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def get(self, prop_id: int) -> float:
        if self._cap is None:
            return 0.0
        return float(self._cap.get(prop_id))
