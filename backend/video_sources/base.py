from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple
import numpy as np

class VideoSourceError(Exception):
    code: str = "source_error"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        if code:
            self.code = code
        self.message = message


class ConnectionTimeoutError(VideoSourceError):
    code = "connection_timeout"


class AuthenticationFailedError(VideoSourceError):
    code = "authentication_failed"


class CameraUnreachableError(VideoSourceError):
    code = "camera_unreachable"


class InvalidStreamUrlError(VideoSourceError):
    code = "invalid_rtsp_url"


class WebcamUnavailableError(VideoSourceError):
    code = "webcam_unavailable"


class VideoSource(ABC):
    source_kind: str = "unknown"

    @abstractmethod
    def connect(self) -> None:
        """Open the underlying capture. Raises VideoSourceError on failure."""

    @abstractmethod
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Grab the next frame.
        Returns (True, frame_bgr) on success, (False, None) on failure/EOF.
        """

    @abstractmethod
    def release(self) -> None:
        """Release native resources. Safe to call multiple times."""

    @abstractmethod
    def isOpened(self) -> bool:
        """Whether the source is currently open and readable."""

    def get(self, prop_id: int) -> float:
        """Optional OpenCV-style property getter. Default returns 0."""
        return 0.0

    @property
    def label(self) -> str:
        """Human-readable source description for status UIs."""
        return self.source_kind

    def __enter__(self) -> "VideoSource":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()