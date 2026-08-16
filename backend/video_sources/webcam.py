from __future__ import annotations
import logging
import sys
from typing import Optional
import cv2
from video_sources.base import VideoSource, WebcamUnavailableError

logger = logging.getLogger("netra.video.webcam")

class WebcamSource(VideoSource):
    source_kind = "webcam"

    def __init__(self, device_index: int = 0, *, width: int = 1280, height: int = 720):
        self._index = int(device_index)
        self._width = width
        self._height = height
        self._cap: Optional[cv2.VideoCapture] = None

    @property
    def label(self) -> str:
        return f"webcam:{self._index}"

    def connect(self) -> None:
        self.release()
        backends = []
        if sys.platform.startswith("win"):
            backends.append(cv2.CAP_DSHOW)
        backends.append(cv2.CAP_ANY)

        last_err = None
        for backend in backends:
            try:
                cap = cv2.VideoCapture(self._index, backend)
            except Exception as exc:
                last_err = exc
                continue

            if not cap.isOpened():
                cap.release()
                continue

            if self._width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            if self._height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)

            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue

            self._cap = cap
            logger.info("Webcam connected on index %s (backend=%s)", self._index, backend)
            return

        raise WebcamUnavailableError(
            f"Webcam unavailable (device index {self._index}). "
            "Check that a camera is connected and not used by another app."
            + (f" Detail: {last_err}" if last_err else "")
        )

    def read(self):
        if self._cap is None or not self._cap.isOpened():
            return False, None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return False, None
        return True, frame

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.exception("Error releasing webcam")
            self._cap = None

    def isOpened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def get(self, prop_id: int) -> float:
        if self._cap is None:
            return 0.0
        return float(self._cap.get(prop_id))