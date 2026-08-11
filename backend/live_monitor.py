"""
live_monitor.py
---------------
Session manager for Live CCTV / webcam streams.

While connected, a background loop continuously grabs frames so the
dashboard shows live video (not a single still). Start Monitoring turns
on shared AI inference via frame_processor.process_frame.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import cv2
import numpy as np

import analysis_pipeline
import frame_processor
from video_sources.base import VideoSource, VideoSourceError

logger = logging.getLogger("netra.live_monitor")


class LiveMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._source: Optional[VideoSource] = None
        self._connected = False
        self._monitoring = False  # AI on/off
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_event = threading.Event()
        self._frame_version = 0

        self._frame_count = 0
        self._fps = 0.0
        self._processing_status = "idle"
        self._connection_status = "disconnected"
        self._error: Optional[str] = None
        self._error_code: Optional[str] = None
        self._resolution = "—"
        self._job_id: Optional[str] = None
        self._latest_jpeg: Optional[bytes] = None
        self._latest_meta: Dict[str, Any] = {}
        self._source_label = "—"

        # This session's own AI pipeline state (frame counter, last
        # detections, violence buffer, scene-change baseline) -- created
        # fresh on every connect() so a new live session never inherits
        # stale state from whatever was connected before it, and so this
        # doesn't collide with any file-upload job's FrameProcessor
        # running concurrently. See frame_processor.FrameProcessor.
        self._processor = frame_processor.FrameProcessor(label="live")

        # Edge-trigger flags so live detections emit one alert per
        # appearance (same semantics as file analysis), then ride the
        # Sub-5s webhook pipeline with snapshot context.
        self._active_state = {
            "weapon": False,
            "plate": False,
            "anomaly": False,
            "violence": False,
        }
        self._last_plate_key: Optional[str] = None
        self._loop_started_monotonic = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self, source: VideoSource, user_id: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            self._stop_loop_unlocked(join=True)
            self._release_source_unlocked()

            try:
                source.connect()
            except VideoSourceError as exc:
                self._connection_status = "error"
                self._error = exc.message
                self._error_code = exc.code
                self._processing_status = "idle"
                raise
            except Exception as exc:
                self._connection_status = "error"
                self._error = f"Connection failed: {exc}"
                self._error_code = "source_error"
                raise VideoSourceError(self._error) from exc

            self._source = source
            self._connected = True
            self._connection_status = "connected"
            self._error = None
            self._error_code = None
            self._frame_count = 0
            self._fps = 0.0
            self._monitoring = False
            self._processing_status = "preview"
            self._source_label = source.label
            self._resolution = self._read_resolution(source)

            # Fresh AI pipeline state for this new session -- see __init__.
            self._processor = frame_processor.FrameProcessor(label=source.label)

            analysis = analysis_pipeline.queue_for_analysis(
                source=analysis_pipeline.SOURCE_LIVE,
                stream_url=source.label,
                original_name=source.label,
                extra={"source_kind": source.source_kind, "user_id": user_id},
            )
            self._job_id = analysis.get("job_id")
            analysis_pipeline.update_job(
                self._job_id,
                status="connected",
                message=f"Live source connected: {source.label}. Preview streaming.",
            )

            # Continuous capture starts immediately — live video, not one still.
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._capture_loop,
                name="netra-live-capture",
                daemon=True,
            )
            self._thread.start()

            return self.status()

    def disconnect(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_loop_unlocked(join=True)
            self._release_source_unlocked()
            self._connected = False
            self._monitoring = False
            self._connection_status = "disconnected"
            self._processing_status = "idle"
            self._source_label = "—"
            self._resolution = "—"
            self._fps = 0.0
            self._latest_jpeg = None
            if self._job_id:
                analysis_pipeline.update_job(
                    self._job_id,
                    status="disconnected",
                    message="Live source disconnected.",
                )
            return self.status()

    def start_monitoring(self) -> Dict[str, Any]:
        with self._lock:
            if not self._connected or self._source is None or not self._source.isOpened():
                raise VideoSourceError(
                    "No live source connected. Connect before starting monitoring.",
                    code="not_connected",
                )
            self._monitoring = True
            self._processing_status = "processing"
            self._error = None
            self._error_code = None
            if self._job_id:
                analysis_pipeline.update_job(
                    self._job_id,
                    status="processing",
                    message="AI monitoring on — frames flowing through shared frame_processor.",
                )
            return self.status()

    def stop_monitoring(self, join: bool = False) -> Dict[str, Any]:
        # join kept for API compat; capture loop keeps running while connected.
        with self._lock:
            self._monitoring = False
            if self._connected:
                self._processing_status = "preview"
                if self._job_id:
                    analysis_pipeline.update_job(
                        self._job_id,
                        status="connected",
                        message="AI monitoring stopped. Live preview still running.",
                    )
            elif self._processing_status == "processing":
                self._processing_status = "idle"
            return self.status()

    # ------------------------------------------------------------------
    # Status / frames
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            fp_stats = self._processor.get_stats()
            return {
                "connected": self._connected,
                "monitoring": self._monitoring,
                "connection_status": self._connection_status,
                "processing_status": self._processing_status,
                "current_source": self._source_label,
                "source_kind": self._source.source_kind if self._source else None,
                "frame_count": self._frame_count,
                "fps": round(self._fps, 1),
                "resolution": self._resolution,
                "error": self._error,
                "error_code": self._error_code,
                "job_id": self._job_id,
                "model_status": frame_processor.model_status(),
                "has_frame": self._latest_jpeg is not None,
                "frame_version": self._frame_version,
                "last_inference_ms": fp_stats.get("last_inference_ms"),
                "detections_last_frame": fp_stats.get("detections_last_frame"),
            }

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def wait_for_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        """Block until a newer frame is available (for MJPEG)."""
        self._frame_event.wait(timeout=timeout)
        self._frame_event.clear()
        return self.get_jpeg()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _stop_loop_unlocked(self, join: bool = False) -> None:
        self._stop_event.set()
        self._monitoring = False
        thread = self._thread
        self._thread = None
        if join and thread is not None and thread.is_alive():
            # Release lock while joining so the capture loop can finish.
            self._lock.release()
            try:
                thread.join(timeout=3.0)
            finally:
                self._lock.acquire()

    def _release_source_unlocked(self) -> None:
        if self._source is not None:
            try:
                self._source.release()
            except Exception:
                logger.exception("Error releasing live source")
            self._source = None

    @staticmethod
    def _read_resolution(source: VideoSource) -> str:
        w = int(source.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(source.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if w > 0 and h > 0:
            return f"{w}×{h}"
        return "—"

    def _store_jpeg(self, frame: np.ndarray, quality: int = 75) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            with self._lock:
                self._latest_jpeg = buf.tobytes()
                self._frame_version += 1
            self._frame_event.set()

    def _emit_live_events(self, frame, meta: Dict[str, Any], label: str) -> None:
        """Translate live frame meta into analysis events → alerting."""
        job_id = self._job_id
        if not job_id or not meta:
            return

        import uuid
        from datetime import datetime, timezone

        frame_h, frame_w = frame.shape[:2]
        video_time = max(0.0, time.perf_counter() - (self._loop_started_monotonic or time.perf_counter()))

        def _log(event_type, label_text, confidence, extra=None, snap_dets=None):
            event_id = uuid.uuid4().hex[:12]
            snapshot_url = analysis_pipeline._save_snapshot(
                job_id, event_id, frame, detections=snap_dets
            )
            event = {
                "event_id": event_id,
                "type": event_type,
                "label": label_text,
                "confidence": round(float(confidence or 0.0), 4),
                "frame_number": self._frame_count,
                "video_time_seconds": round(video_time, 3),
                "video_timestamp": analysis_pipeline.format_video_timestamp(video_time),
                "wall_clock_time": datetime.now(timezone.utc).isoformat(),
                "snapshot_url": snapshot_url,
            }
            if extra:
                event.update(extra)
            analysis_pipeline.add_event(job_id, event)

        weapon_dets = meta.get("weapon_detections") or []
        weapon_now = bool(weapon_dets)
        if weapon_now and not self._active_state["weapon"]:
            top = max(weapon_dets, key=lambda d: d.get("confidence", 0.0))
            bbox = top.get("bbox")
            _log(
                "weapon",
                top.get("label", "weapon"),
                top.get("confidence", 0.0),
                {
                    "bbox": bbox,
                    "location": analysis_pipeline._describe_location(
                        bbox, frame_w, frame_h, label
                    ),
                },
                snap_dets=weapon_dets,
            )
        self._active_state["weapon"] = weapon_now

        ocr_dets = meta.get("ocr_detections") or []
        plate_now = bool(ocr_dets)
        if plate_now:
            top = max(ocr_dets, key=lambda d: d.get("confidence", 0.0))
            plate_text = (
                top.get("plate_number")
                or top.get("text")
                or top.get("label")
                or "PLATE"
            )
            plate_key = str(plate_text).strip().upper()
            if (not self._active_state["plate"]) or (plate_key != self._last_plate_key):
                bbox = top.get("bbox")
                _log(
                    "plate",
                    plate_text,
                    top.get("confidence", 0.0),
                    {
                        "bbox": bbox,
                        "plate_number": top.get("plate_number"),
                        "ocr_confidence": top.get("ocr_confidence"),
                        "location": analysis_pipeline._describe_location(
                            bbox, frame_w, frame_h, label
                        ),
                    },
                    snap_dets=ocr_dets,
                )
                self._last_plate_key = plate_key
        else:
            self._last_plate_key = None
        self._active_state["plate"] = plate_now

        anomaly = meta.get("anomaly") or {}
        anomaly_now = bool(anomaly.get("detected"))
        if anomaly_now and not self._active_state["anomaly"]:
            _log(
                "anomaly",
                "Anomalous activity",
                0.0,
                {
                    "reconstruction_error": anomaly.get("error"),
                    "threshold": anomaly.get("threshold"),
                    "location": analysis_pipeline._describe_location(
                        None, frame_w, frame_h, label
                    ),
                },
            )
        self._active_state["anomaly"] = anomaly_now

        violence = meta.get("violence") or {}
        violence_now = str(violence.get("prediction") or "").upper() == "FIGHT"
        if violence_now and not self._active_state["violence"]:
            _log(
                "violence",
                "Fight / violent activity",
                violence.get("confidence", 0.0),
                {
                    "location": analysis_pipeline._describe_location(
                        None, frame_w, frame_h, label
                    ),
                },
            )
        self._active_state["violence"] = violence_now

    def _capture_loop(self) -> None:
        logger.info("Live capture loop started for %s", self._source_label)
        window_frames = 0
        window_t0 = time.perf_counter()
        self._loop_started_monotonic = time.perf_counter()
        self._active_state = {
            "weapon": False,
            "plate": False,
            "anomaly": False,
            "violence": False,
        }
        self._last_plate_key = None

        while not self._stop_event.is_set():
            with self._lock:
                source = self._source
                label = self._source_label
                connected = self._connected
                ai_on = self._monitoring

            if not connected or source is None or not source.isOpened():
                break

            ok, frame = source.read()
            if not ok or frame is None:
                # Brief retry — webcams can drop a frame without dying.
                time.sleep(0.02)
                ok, frame = source.read()
                if not ok or frame is None:
                    with self._lock:
                        self._error = "Stream ended or frame grab failed."
                        self._error_code = "frame_read_failed"
                        self._processing_status = "error"
                        self._monitoring = False
                        self._connection_status = "error"
                        self._connected = False
                    if self._job_id:
                        analysis_pipeline.update_job(
                            self._job_id,
                            status="error",
                            message=self._error,
                        )
                    break

            try:
                if ai_on:
                    annotated, meta = self._processor.process_frame(
                        frame, source_label=label, draw=True
                    )
                    try:
                        self._emit_live_events(frame, meta, label)
                    except Exception:
                        logger.exception("Live alert emit failed")
                else:
                    # Light HUD so preview is clearly live (no AI yet).
                    annotated = frame.copy()
                    cv2.putText(
                        annotated,
                        f"PREVIEW  {label}",
                        (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (63, 232, 208),
                        2,
                        cv2.LINE_AA,
                    )
                    meta = {}
            except Exception as exc:
                logger.exception("Frame processing failed")
                with self._lock:
                    self._error = f"Processing failed: {exc}"
                    self._error_code = "processing_failed"
                    self._processing_status = "error"
                    self._monitoring = False
                # Keep preview alive even if one AI call fails
                annotated = frame
                meta = {}

            self._store_jpeg(annotated)
            with self._lock:
                self._frame_count += 1
                self._latest_meta = meta
                window_frames += 1
                elapsed = time.perf_counter() - window_t0
                if elapsed >= 1.0:
                    self._fps = window_frames / elapsed
                    window_frames = 0
                    window_t0 = time.perf_counter()

            # Cap preview roughly ~20–25 FPS when AI is off; AI path is self-limiting.
            time.sleep(0.001 if ai_on else 0.04)

        with self._lock:
            if self._processing_status in ("processing", "preview"):
                self._processing_status = "idle" if not self._connected else self._processing_status
            self._monitoring = False
        logger.info("Live capture loop stopped")


# Process-wide singleton used by the live API routes.
monitor = LiveMonitor()