from __future__ import annotations
import logging
import re
from typing import Optional
from urllib.parse import quote

import cv2

from video_sources.base import (
    AuthenticationFailedError,
    CameraUnreachableError,
    ConnectionTimeoutError,
    InvalidStreamUrlError,
    VideoSource,
    VideoSourceError,
)

logger = logging.getLogger("netra.video.rtsp")

BRAND_HIKVISION = "hikvision"
BRAND_DAHUA = "dahua"
BRAND_CP_PLUS = "cp_plus"
BRAND_CUSTOM = "custom"

SUPPORTED_BRANDS = (BRAND_HIKVISION, BRAND_DAHUA, BRAND_CP_PLUS, BRAND_CUSTOM)

_RTSP_RE = re.compile(r"^rtsps?://", re.IGNORECASE)


def build_rtsp_url(
    brand: str,
    *,
    ip: str,
    port: int = 554,
    username: str = "",
    password: str = "",
    channel: int = 1,
    subtype: int = 0,
) -> str:
    brand_key = (brand or "").strip().lower().replace("-", "_").replace(" ", "_")
    if brand_key in ("cpplus", "cp-plus"):
        brand_key = BRAND_CP_PLUS

    ip = (ip or "").strip()
    if not ip:
        raise InvalidStreamUrlError("IP address is required.")

    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise InvalidStreamUrlError("Port must be a number.") from exc

    user = quote(username or "", safe="")
    pwd = quote(password or "", safe="")
    auth = f"{user}:{pwd}@" if (username or password) else ""

    if brand_key == BRAND_HIKVISION:
        # Channel 1 main stream -> 101
        stream_id = channel * 100 + 1
        return f"rtsp://{auth}{ip}:{port}/Streaming/Channels/{stream_id}"

    if brand_key in (BRAND_DAHUA, BRAND_CP_PLUS):
        return (
            f"rtsp://{auth}{ip}:{port}/cam/realmonitor"
            f"?channel={channel}&subtype={subtype}"
        )

    raise InvalidStreamUrlError(
        f"Unsupported brand '{brand}'. Use hikvision, dahua, cp_plus, or custom."
    )


def validate_rtsp_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        raise InvalidStreamUrlError("RTSP URL is required.")
    if not _RTSP_RE.match(cleaned):
        raise InvalidStreamUrlError(
            "Invalid RTSP URL. Expected a URL starting with rtsp:// or rtsps://"
        )
    return cleaned


def _classify_open_failure(url: str, detail: str = "") -> VideoSourceError:
    lowered = f"{url} {detail}".lower()
    if any(tok in lowered for tok in ("401", "403", "auth", "unauthorized", "password")):
        return AuthenticationFailedError(
            "Authentication failed. Check username and password."
        )
    if any(tok in lowered for tok in ("timed out", "timeout", "time out")):
        return ConnectionTimeoutError(
            "Connection timed out. Camera did not respond in time."
        )
    if any(tok in lowered for tok in ("resolve", "unreachable", "no route", "network")):
        return CameraUnreachableError(
            "Camera not reachable. Check IP address, port, and network."
        )
    return CameraUnreachableError(
        "Camera not reachable or stream refused. Verify IP, port, credentials, and brand path."
    )


class RTSPSource(VideoSource):
    source_kind = "rtsp"

    def __init__(
        self,
        url: str,
        *,
        open_timeout_ms: int = 8000,
        read_timeout_ms: int = 5000,
        label: Optional[str] = None,
    ):
        self._url = validate_rtsp_url(url)
        self._open_timeout_ms = open_timeout_ms
        self._read_timeout_ms = read_timeout_ms
        self._cap: Optional[cv2.VideoCapture] = None
        self._label = label or self._redact(self._url)

    @staticmethod
    def _redact(url: str) -> str:
        return re.sub(r"(rtsps?://)([^:@/]+):([^@/]+)@", r"\1***:***@", url, flags=re.I)

    @property
    def label(self) -> str:
        return self._label

    @property
    def url(self) -> str:
        return self._url

    def connect(self) -> None:
        self.release()
        cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, float(self._open_timeout_ms))
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, float(self._read_timeout_ms))
        except Exception:
            pass
        # Low-latency buffer
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:
            pass

        if not cap.isOpened():
            cap.release()
            raise _classify_open_failure(self._url)

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise _classify_open_failure(
                self._url, detail="opened but failed to read first frame"
            )

        self._cap = cap
        logger.info("RTSP connected: %s", self._label)

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
                logger.exception("Error releasing RTSP capture")
            self._cap = None

    def isOpened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def get(self, prop_id: int) -> float:
        if self._cap is None:
            return 0.0
        return float(self._cap.get(prop_id))