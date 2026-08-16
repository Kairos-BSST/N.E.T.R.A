from __future__ import annotations
import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from live_monitor import monitor
from auth import current_user
import database
from video_sources import (
    BRAND_CP_PLUS,
    BRAND_CUSTOM,
    BRAND_DAHUA,
    BRAND_HIKVISION,
    RTSPSource,
    WebcamSource,
    VideoSourceError,
    build_rtsp_url,
    validate_rtsp_url,
)

logger = logging.getLogger("netra.live")
router = APIRouter(tags=["live"])

Method = Literal["brand", "custom", "webcam"]
Brand = Literal["hikvision", "dahua", "cp_plus", "custom"]


class LiveConnectRequest(BaseModel):
    method: Method = "brand"
    brand: Optional[Brand] = BRAND_HIKVISION
    ip: Optional[str] = None
    port: int = 554
    username: Optional[str] = None
    password: Optional[str] = None
    channel: int = 1
    subtype: int = 0
    rtsp_url: Optional[str] = None
    webcam_index: int = 0


class BuildUrlRequest(BaseModel):
    brand: Brand = BRAND_HIKVISION
    ip: str
    port: int = 554
    username: str = ""
    password: str = ""
    channel: int = 1
    subtype: int = 0


def _http_error_from_source(exc: VideoSourceError) -> HTTPException:
    status = 400
    if exc.code in ("camera_unreachable", "connection_timeout", "webcam_unavailable"):
        status = 503
    elif exc.code == "authentication_failed":
        status = 401
    elif exc.code == "not_connected":
        status = 409
    return HTTPException(
        status_code=status,
        detail={"message": exc.message, "code": exc.code},
    )


def _build_source(req: LiveConnectRequest):
    method = (req.method or "brand").lower()

    if method == "webcam":
        return WebcamSource(req.webcam_index)

    if method == "custom" or (req.brand or "").lower() == BRAND_CUSTOM:
        url = validate_rtsp_url(req.rtsp_url or "")
        return RTSPSource(url, label=f"custom:{url.split('@')[-1] if '@' in url else url}")

    # Brand form (recommended)
    brand = (req.brand or BRAND_HIKVISION).lower()
    if brand == BRAND_CUSTOM:
        url = validate_rtsp_url(req.rtsp_url or "")
        return RTSPSource(url)

    url = build_rtsp_url(
        brand,
        ip=req.ip or "",
        port=req.port,
        username=req.username or "",
        password=req.password or "",
        channel=req.channel,
        subtype=req.subtype,
    )
    brand_label = {
        BRAND_HIKVISION: "Hikvision",
        BRAND_DAHUA: "Dahua",
        BRAND_CP_PLUS: "CP Plus",
    }.get(brand, brand)
    return RTSPSource(url, label=f"{brand_label} {req.ip}:{req.port}")


@router.post("/live/build-url")
def build_url(req: BuildUrlRequest, user=Depends(current_user)):
    """Preview the RTSP URL for a brand form without connecting."""
    try:
        url = build_rtsp_url(
            req.brand,
            ip=req.ip,
            port=req.port,
            username=req.username,
            password=req.password,
            channel=req.channel,
            subtype=req.subtype,
        )
    except VideoSourceError as exc:
        raise _http_error_from_source(exc) from exc
    # Redact credentials in response
    redacted = url
    if "@" in url:
        scheme, rest = url.split("://", 1)
        redacted = f"{scheme}://***:***@{rest.split('@', 1)[-1]}"
    return {"rtsp_url": url, "rtsp_url_redacted": redacted}


@router.post("/live/connect")
def live_connect(req: LiveConnectRequest, user=Depends(current_user)):
    """Open RTSP or webcam via VideoSource.connect() and verify first frame."""
    try:
        source = _build_source(req)
        status = monitor.connect(source, user_id=user["id"])
    except VideoSourceError as exc:
        logger.warning("Live connect failed: %s", exc)
        raise _http_error_from_source(exc) from exc
    except Exception as exc:
        logger.exception("Unexpected live connect error")
        raise HTTPException(status_code=500, detail={"message": str(exc), "code": "source_error"}) from exc

    database.record_audit(
        user["id"], "CCTV_CONNECTED", job_id=status.get("job_id"),
        resource_type="live_source", resource_id=status.get("job_id"),
        details={"source": status.get("current_source"), "method": req.method, "brand": req.brand},
    )
    return {"status": "connected", "live": status}


@router.post("/live/disconnect")
def live_disconnect(user=Depends(current_user)):
    status = monitor.disconnect()
    return {"status": "disconnected", "live": status}


@router.post("/live/start")
def live_start(user=Depends(current_user)):
    """Start continuous read → shared AI process_frame → dashboard stream."""
    try:
        status = monitor.start_monitoring()
    except VideoSourceError as exc:
        raise _http_error_from_source(exc) from exc
    return {"status": "monitoring", "live": status}


@router.post("/live/stop")
def live_stop(user=Depends(current_user)):
    status = monitor.stop_monitoring(join=True)
    return {"status": "stopped", "live": status}


@router.get("/live/status")
def live_status(user=Depends(current_user)):
    return monitor.status()


@router.get("/live/frame")
def live_frame(user=Depends(current_user)):
    """Latest JPEG snapshot — polled by the dashboard for reliable live video."""
    jpeg = monitor.get_jpeg()
    if not jpeg:
        raise HTTPException(status_code=404, detail="No frame available yet.")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.get("/live/stream")
def live_stream(user=Depends(current_user)):
    def generate():
        last_version = -1
        while True:
            status = monitor.status()
            if not status.get("connected") and not status.get("has_frame"):
                time.sleep(0.15)
                continue

            version = status.get("frame_version", 0)
            jpeg = monitor.get_jpeg()
            if jpeg and version != last_version:
                last_version = version
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                )
            else:
                # Wait for a new frame instead of flooding the same JPEG.
                monitor.wait_for_frame(timeout=0.5)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )

class LiveAnalyzeRequest(BaseModel):
    stream_url: str = Field(..., min_length=1)


@router.post("/analysis/live")
def queue_live_analysis(req: LiveAnalyzeRequest, user=Depends(current_user)):
    import analysis_pipeline

    url = (req.stream_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="stream_url is required.")
    analysis = analysis_pipeline.queue_for_analysis(
        source=analysis_pipeline.SOURCE_LIVE,
        stream_url=url,
        original_name=url,
        extra={"user_id": user["id"]},
    )
    return {"status": "queued", "analysis": analysis}