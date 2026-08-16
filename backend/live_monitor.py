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
        self._user_id: Optional[int] = None
        self._source_kind: Optional[str] = None
        self._latest_jpeg: Optional[bytes] = None
        self._latest_meta: Dict[str, Any] = {}
        self._source_label = "—"
        self._processor = frame_processor.FrameProcessor(label="live")
        self._active_state = {
            "weapon": False,
            "plate": False,
            "anomaly": False,
            "violence": False,
            "face": False,
        }
        self._last_plate_key: Optional[str] = None
        self._last_face_key: Optional[str] = None
        self._loop_started_monotonic = 0.0
        self._session_started_monotonic = 0.0
        self._session_frames = 0

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
            self._source_kind = source.source_kind
            self._user_id = user_id
            self._resolution = self._read_resolution(source)
            self._job_id = None
            self._session_frames = 0
            self._session_started_monotonic = 0.0

            self._processor = frame_processor.FrameProcessor(label=source.label)

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
            was_monitoring = self._monitoring
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
            if self._job_id and was_monitoring:
                self._finalize_session_unlocked(
                    message="Live source disconnected — monitoring session closed. Report covers detections until disconnect."
                )
            elif self._job_id:
                job = analysis_pipeline.get_job(self._job_id)
                if job and job.get("status") not in {"completed", "failed"}:
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

            # Each Start→Stop cycle is one reportable session.
            needs_new_job = True
            if self._job_id:
                existing = analysis_pipeline.get_job(self._job_id)
                if existing and existing.get("status") == "processing":
                    needs_new_job = False

            if needs_new_job:
                analysis = analysis_pipeline.queue_for_analysis(
                    source=analysis_pipeline.SOURCE_LIVE,
                    stream_url=self._source_label,
                    original_name=self._source_label,
                    extra={
                        "source_kind": self._source_kind,
                        "user_id": self._user_id,
                    },
                )
                self._job_id = analysis.get("job_id")

            self._reset_detection_state_unlocked()
            self._session_started_monotonic = time.perf_counter()
            self._session_frames = 0
            self._monitoring = True
            self._processing_status = "processing"
            self._error = None
            self._error_code = None
            analysis_pipeline.update_job(
                self._job_id,
                status="processing",
                started_at=analysis_pipeline._utc_now(),
                message="AI monitoring on — detections and report events run until Stop Monitoring.",
            )
            return self.status()

    def stop_monitoring(self, join: bool = False) -> Dict[str, Any]:
        # join kept for API compat; capture loop keeps running while connected.
        with self._lock:
            was_monitoring = self._monitoring
            self._monitoring = False
            if was_monitoring and self._job_id:
                self._finalize_session_unlocked(
                    message="Monitoring stopped. Report includes detections until Stop was clicked."
                )
            if self._connected:
                self._processing_status = "preview"
            elif self._processing_status == "processing":
                self._processing_status = "idle"
            return self.status()

    def _reset_detection_state_unlocked(self) -> None:
        self._active_state = {
            "weapon": False,
            "plate": False,
            "anomaly": False,
            "violence": False,
            "face": False,
        }
        self._last_plate_key = None
        self._last_face_key = None

    def _finalize_session_unlocked(self, *, message: str) -> None:
        """Mark the active live job completed so the PDF/report covers this session only."""
        job_id = self._job_id
        if not job_id:
            return
        job = analysis_pipeline.get_job(job_id) or {}
        if job.get("status") in {"completed", "failed"}:
            return

        events = job.get("events") or []
        plates_seen: Dict[str, Any] = {}
        weapon_detections = 0
        plate_detections = 0
        anomaly_frames = 0
        fight_frames = 0
        face_detections = 0
        for ev in events:
            et = (ev.get("type") or "").lower()
            if et == "weapon":
                weapon_detections += 1
            elif et == "plate":
                plate_detections += 1
                plate = (ev.get("plate_number") or ev.get("label") or "").strip()
                if plate:
                    plates_seen[plate.upper()] = {
                        "plate_number": plate,
                        "confidence": ev.get("confidence"),
                    }
            elif et == "anomaly":
                anomaly_frames += 1
            elif et == "violence":
                fight_frames += 1
            elif et == "face":
                face_detections += 1

        started = self._session_started_monotonic or time.perf_counter()
        processing_seconds = max(0.0, time.perf_counter() - started)
        frames_processed = int(self._session_frames or 0)

        result = {
            "frames_processed": frames_processed,
            "processing_seconds": round(processing_seconds, 2),
            "total_detections": len(events),
            "weapon_detections": weapon_detections,
            "plate_detections": plate_detections,
            "anomaly_frames": anomaly_frames,
            "fight_frames": fight_frames,
            "face_detections": face_detections,
            "plates_found": list(plates_seen.values()),
            "source": "live",
            "ended_by": "stop_monitoring",
        }

        analysis_pipeline.update_job(
            job_id,
            status="completed",
            message=message,
            progress=100.0,
            frames_processed=frames_processed,
            completed_at=analysis_pipeline._utc_now(),
            result=result,
            error=None,
        )

    # Status / frames
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

    # Internals
    def _stop_loop_unlocked(self, join: bool = False) -> None:
        self._stop_event.set()
        self._monitoring = False
        thread = self._thread
        self._thread = None
        if join and thread is not None and thread.is_alive():
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
        with self._lock:
            job_id = self._job_id
            monitoring = self._monitoring
        if not job_id or not meta or not monitoring:
            return

        job = analysis_pipeline.get_job(job_id) or {}
        if job.get("status") in {"completed", "failed", "disconnected"}:
            return

        import uuid
        from datetime import datetime, timezone

        frame_h, frame_w = frame.shape[:2]
        session_t0 = self._session_started_monotonic or self._loop_started_monotonic or time.perf_counter()
        video_time = max(0.0, time.perf_counter() - session_t0)

        def _snapshot_dets(*extra):
            dets = []
            dets.extend(meta.get("weapon_detections") or [])
            dets.extend(meta.get("ocr_detections") or [])
            dets.extend(meta.get("face_detections") or [])
            for item in extra:
                if item:
                    dets.append(item)
            return dets

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
                snap_dets=_snapshot_dets(),
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
                    snap_dets=_snapshot_dets(),
                )
                self._last_plate_key = plate_key
        else:
            self._last_plate_key = None
        self._active_state["plate"] = plate_now

        anomaly = meta.get("anomaly") or {}
        anomaly_now = bool(anomaly.get("detected"))
        if anomaly_now and not self._active_state["anomaly"]:
            frame_det = frame_processor.make_frame_level_detection(
                frame_w, frame_h, "anomaly", "Anomalous activity",
                confidence=float(anomaly.get("error") or 0.0),
            )
            _log(
                "anomaly",
                "Anomalous activity",
                0.0,
                {
                    "reconstruction_error": anomaly.get("error"),
                    "threshold": anomaly.get("threshold"),
                    "bbox": frame_det["bbox"],
                    "location": analysis_pipeline._describe_location(
                        frame_det["bbox"], frame_w, frame_h, label
                    ),
                },
                snap_dets=_snapshot_dets(frame_det),
            )
        self._active_state["anomaly"] = anomaly_now

        violence = meta.get("violence") or {}
        violence_now = str(violence.get("prediction") or "").upper() == "FIGHT"
        if violence_now and not self._active_state["violence"]:
            conf = float(violence.get("confidence") or 0.0)
            frame_det = frame_processor.make_frame_level_detection(
                frame_w, frame_h, "violence", "Fight / violent activity", confidence=conf,
            )
            _log(
                "violence",
                "Fight / violent activity",
                conf,
                {
                    "bbox": frame_det["bbox"],
                    "location": analysis_pipeline._describe_location(
                        frame_det["bbox"], frame_w, frame_h, label
                    ),
                },
                snap_dets=_snapshot_dets(frame_det),
            )
        self._active_state["violence"] = violence_now

        face_dets = meta.get("face_detections") or []
        face_now = bool(face_dets)
        if face_now:
            top = max(face_dets, key=lambda d: d.get("confidence", 0.0))
            face_key = str(top.get("poi_id") or top.get("label") or "face")
            if (not self._active_state["face"]) or (face_key != self._last_face_key):
                bbox = top.get("bbox")
                _log(
                    "face",
                    top.get("label") or "Person of interest",
                    top.get("confidence", 0.0),
                    {
                        "bbox": bbox,
                        "poi_id": top.get("poi_id"),
                        "face_id": top.get("face_id"),
                        "similarity": top.get("similarity", top.get("confidence")),
                        "location": analysis_pipeline._describe_location(
                            bbox, frame_w, frame_h, label
                        ),
                    },
                    snap_dets=_snapshot_dets(),
                )
                self._last_face_key = face_key
        else:
            self._last_face_key = None
        self._active_state["face"] = face_now

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
            "face": False,
        }
        self._last_plate_key = None
        self._last_face_key = None

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
                    with self._lock:
                        still_on = self._monitoring and self._job_id is not None
                    if still_on:
                        try:
                            self._emit_live_events(frame, meta, label)
                        except Exception:
                            logger.exception("Live alert emit failed")
                    else:
                        # Stop clicked mid-inference — show preview label, no new events.
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
                else:
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
                annotated = frame
                meta = {}

            self._store_jpeg(annotated)
            with self._lock:
                self._frame_count += 1
                if ai_on:
                    self._session_frames += 1
                self._latest_meta = meta
                window_frames += 1
                elapsed = time.perf_counter() - window_t0
                if elapsed >= 1.0:
                    self._fps = window_frames / elapsed
                    window_frames = 0
                    window_t0 = time.perf_counter()
            time.sleep(0.001 if ai_on else 0.04)

        with self._lock:
            if self._processing_status in ("processing", "preview"):
                self._processing_status = "idle" if not self._connected else self._processing_status
            self._monitoring = False
        logger.info("Live capture loop stopped")

monitor = LiveMonitor()