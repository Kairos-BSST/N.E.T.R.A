from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from config import Config
import database
from webhook_client import send_event_webhook

logger = logging.getLogger("netra.analysis")


# Source kinds
SOURCE_UPLOAD = "upload"
SOURCE_DRIVE = "drive"
SOURCE_LIVE = "live"
SOURCE_WEBCAM = "webcam"

def _get_analysis_fps(source_fps: float) -> float:
    configured = float(getattr(Config, "VIDEO_ANALYSIS_FPS", 8.0) or 8.0)
    if configured <= 0:
        configured = 8.0
    if source_fps <= 0:
        return configured
    return min(source_fps, configured)

# Job registry
_lock = threading.RLock()
_jobs: Dict[str, dict] = {}
_workers: Dict[str, threading.Thread] = {}
_cancel_flags: Dict[str, threading.Event] = {}


def _cancel_event(job_id: str) -> threading.Event:
    with _lock:
        event = _cancel_flags.get(job_id)
        if event is None:
            event = threading.Event()
            _cancel_flags[job_id] = event
        return event


def _is_cancelled(job_id: str) -> bool:
    event = _cancel_flags.get(job_id)
    return bool(event and event.is_set())


def _clear_cancel(job_id: str) -> None:
    with _lock:
        _cancel_flags.pop(job_id, None)

# Helpers
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_job_copy(job: dict) -> dict:
    return dict(job)

def format_video_timestamp(seconds: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"

def _describe_location(
    bbox: Optional[List[int]],
    frame_w: int,
    frame_h: int,
    source_label: str,
) -> str:

    if not bbox or frame_w <= 0 or frame_h <= 0:
        return f"Camera: {source_label}"
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    col = "left" if cx < frame_w / 3 else ("center" if cx < 2 * frame_w / 3 else "right")
    row = "top" if cy < frame_h / 3 else ("middle" if cy < 2 * frame_h / 3 else "bottom")
    return f"Camera: {source_label} — {row}-{col} of frame"

def _snapshot_dir_for_job(job_id: str) -> str:
    path = os.path.join(Config.SNAPSHOT_DIR, job_id)
    os.makedirs(path, exist_ok=True)
    return path

def _save_snapshot(
    job_id: str,
    event_id: str,
    frame: np.ndarray,
    detections: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    try:
        import frame_processor
        out_frame = frame
        if detections:
            out_frame = frame_processor.draw_detections_on_frame(
                frame, detections
            )
        out_dir = _snapshot_dir_for_job(job_id)
        filename = f"{event_id}.jpg"
        full_path = os.path.join(out_dir, filename)
        ok = cv2.imwrite(full_path, out_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None
        try:
            job = get_job(job_id)
            database.add_evidence(
                job_id, (job or {}).get("user_id"), "snapshot", full_path, filename, database.hash_file(full_path)
            )
        except Exception:
            logger.exception("Could not persist snapshot evidence for job_id=%s", job_id)
        return f"/evidence/snapshots/{job_id}/{filename}"
    except Exception:
        logger.exception("Failed to save snapshot for job_id=%s event_id=%s", job_id, event_id)
        return None

def _annotated_video_path(job_id: str) -> str:
    return os.path.join(_snapshot_dir_for_job(job_id), "annotated.mp4")

def add_event(job_id: str, event: Dict[str, Any]) -> None:
    job_copy = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        events = job.setdefault("events", [])
        events.append(event)
        job_copy = dict(job)

    try:
        database.add_event(job_id, event)
    except Exception:
        logger.exception("Could not persist event for job_id=%s", job_id)

    try:
        import alerting
        alerting.emit_from_event(job_id, event, job=job_copy)
    except Exception:
        logger.exception("Alert pipeline failed for job_id=%s — falling back to legacy webhook", job_id)
        send_event_webhook(job_id, event)

# Job creation

def queue_for_analysis(
    *,
    source: str,
    local_path: Optional[str] = None,
    stream_url: Optional[str] = None,
    original_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "user_id": (extra or {}).get("user_id"),
        "source": source,
        "local_path": local_path,
        "stream_url": stream_url,
        "original_name": original_name,
        "status": "queued",
        "message": "Accepted for analysis.",
        "queued_at": _utc_now(),
        "started_at": None,
        "completed_at": None,
        "updated_at": None,
        "progress": 0.0,
        "frames_processed": 0,
        "total_frames": None,
        "result": None,
        "error": None,
        "extra": extra or {},
        "events": [],
    }

    with _lock:
        _jobs[job_id] = job

    try:
        database.record_job(job)
        database.record_audit(
            job.get("user_id"), "ANALYSIS_QUEUED", job_id=job_id,
            resource_type="analysis", resource_id=job_id,
            details={"source": source, "original_name": original_name or "—"},
        )
    except Exception:
        logger.exception("Could not persist queued job %s", job_id)

    logger.info(
        "Analysis queued job_id=%s source=%s path=%s stream=%s",
        job_id,
        source,
        local_path,
        stream_url,
    )

    return _safe_job_copy(job)

# Job registry operations
def update_job(
    job_id: Optional[str],
    **fields: Any,
) -> Optional[dict]:

    if not job_id:
        return None

    previous_status = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            job = database.get_job(job_id)
            if job is None:
                return None
            _jobs[job_id] = job
        previous_status = job.get("status")
        job.update(fields)
        job["updated_at"] = _utc_now()
        result = _safe_job_copy(job)

    try:
        database.record_job(result)
        new_status = result.get("status")
        if new_status in {"completed", "failed"} and new_status != previous_status:
            action = "ANALYSIS_COMPLETED" if new_status == "completed" else "ANALYSIS_FAILED"
            database.record_audit(
                result.get("user_id"), action, job_id=job_id,
                resource_type="analysis", resource_id=job_id,
                details={"original_name": result.get("original_name"), "status": new_status},
            )
    except Exception:
        logger.exception("Could not persist job update %s", job_id)

    return result


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            return _safe_job_copy(job)
    job = database.get_job(job_id)
    if job is not None:
        with _lock:
            _jobs[job_id] = dict(job)
        return _safe_job_copy(job)
    return None


def list_jobs(limit: int = 50) -> List[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda item: item.get("queued_at") or "", reverse=True)
    if len(jobs) < limit:
        try:
            persisted = database.list_jobs(limit=limit)
            seen = {j.get("job_id") for j in jobs}
            jobs.extend(j for j in persisted if j.get("job_id") not in seen)
            jobs.sort(key=lambda item: item.get("queued_at") or "", reverse=True)
        except Exception:
            logger.exception("Could not load persisted jobs")
    return [_safe_job_copy(job) for job in jobs[:limit]]


# Shared AI entry point

def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    import frame_processor
    return frame_processor.process_frame(
        frame,
        source_label=source_label,
        draw=draw,
    )

# Background file analysis
def start_file_analysis(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)

        if job is None:
            raise ValueError(
                f"Unknown analysis job: {job_id}"
            )

        local_path = job.get("local_path")

        if not local_path:
            raise ValueError(
                "Analysis job does not contain a local file path."
            )

        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)

        existing_worker = _workers.get(job_id)

        if (
            existing_worker is not None
            and existing_worker.is_alive()
        ):
            return _safe_job_copy(job)

        if job.get("status") == "completed":
            return _safe_job_copy(job)

        job["status"] = "starting"
        job["message"] = "Starting video analysis."
        job["updated_at"] = _utc_now()
        job["error"] = None
        database.record_job(job)

        worker = threading.Thread(
            target=_file_analysis_worker,
            args=(job_id,),
            name=f"netra-analysis-{job_id[:8]}",
            daemon=True,
        )

        _workers[job_id] = worker

        _clear_cancel(job_id)
        worker.start()

        return _safe_job_copy(job)


def stop_analysis(job_id: str) -> dict:
    """Request early stop for upload/drive file analysis; report covers work until this call."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            job = database.get_job(job_id)
            if job is not None:
                _jobs[job_id] = job
        if job is None:
            raise ValueError(f"Unknown analysis job: {job_id}")

        status = (job.get("status") or "").lower()
        if status in {"completed", "failed"}:
            return _safe_job_copy(job)

        _cancel_event(job_id).set()

        if status in {"queued", "starting"}:
            job.update({
                "status": "completed",
                "message": "Analysis stopped before processing began.",
                "progress": 100.0,
                "completed_at": _utc_now(),
                "result": {
                    "frames_processed": 0,
                    "processing_seconds": 0.0,
                    "total_detections": 0,
                    "weapon_detections": 0,
                    "plate_detections": 0,
                    "anomaly_frames": 0,
                    "fight_frames": 0,
                    "plates_found": [],
                    "ended_by": "stop_analysis",
                },
            })
            job["updated_at"] = _utc_now()
            database.record_job(job)
            return _safe_job_copy(job)

        update_job(
            job_id,
            message="Stop requested — finishing current frame, then closing the report.",
        )
        return get_job(job_id) or _safe_job_copy(job)


def _file_analysis_worker(job_id: str) -> None:
    import frame_processor

    print(f"[ANALYSIS] Worker started: {job_id}", flush=True)
    job = get_job(job_id)

    if job is None:
        return

    local_path = job.get("local_path")

    if not local_path:
        update_job(
            job_id,
            status="failed",
            error="Missing local video path.",
            message="Analysis failed: missing local video path.",
            completed_at=_utc_now(),
        )
        return

    source_label = (
        job.get("original_name")
        or os.path.basename(local_path)
        or job.get("source")
        or "video"
    )

    cap: Optional[cv2.VideoCapture] = None

    started = time.perf_counter()

    frames_processed = 0

    total_detections = 0
    weapon_detections = 0
    plate_detections = 0
    anomaly_frames = 0
    fight_frames = 0

    max_anomaly_error = 0.0
    max_violence_confidence = 0.0

    last_meta: Dict[str, Any] = {}
    plates_seen: Dict[str, Dict[str, Any]] = {}
    last_plate_key: Optional[str] = None
    last_face_key: Optional[str] = None
    active_state: Dict[str, bool] = {
        "weapon": False,
        "plate": False,
        "anomaly": False,
        "violence": False,
        "face": False,
    }

    if not frame_processor.models_ready():
        update_job(
            job_id,
            status="starting",
            message=(
                "Video uploaded successfully. AI models are initializing "
                "in the background; analysis will begin automatically when ready."
            ),
            updated_at=_utc_now(),
        )
        print(
            "[ANALYSIS] Waiting for background AI model initialization...",
            flush=True,
        )
        frame_processor.wait_for_models()
        print("[ANALYSIS] Background AI models are ready.", flush=True)

    if _is_cancelled(job_id):
        update_job(
            job_id,
            status="completed",
            message="Analysis stopped before processing began.",
            progress=100.0,
            completed_at=_utc_now(),
            result={
                "frames_processed": 0,
                "processing_seconds": round(time.perf_counter() - started, 2),
                "total_detections": 0,
                "weapon_detections": 0,
                "plate_detections": 0,
                "anomaly_frames": 0,
                "fight_frames": 0,
                "plates_found": [],
                "ended_by": "stop_analysis",
            },
            error=None,
        )
        _clear_cancel(job_id)
        with _lock:
            _workers.pop(job_id, None)
        return

    processor = frame_processor.FrameProcessor(label=source_label)

    writer: Optional[cv2.VideoWriter] = None
    annotated_rel_url: Optional[str] = None

    try:

        cap = cv2.VideoCapture(local_path)
        print(f"[ANALYSIS] Video opened: {cap.isOpened()}", flush=True)

        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open video: {local_path}"
            )

        raw_total_frames = int(
            cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        )

        total_frames = (
            raw_total_frames
            if raw_total_frames > 0
            else None
        )

        print(f"[ANALYSIS] Total frames: {total_frames}", flush=True)

        fps = float(
            cap.get(cv2.CAP_PROP_FPS) or 0.0
        )

        width = int(
            cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        )

        height = int(
            cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
        )

        out_path = _annotated_video_path(job_id)
        write_fps = fps if fps > 1e-3 else 20.0
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        if width > 0 and height > 0:
            writer = cv2.VideoWriter(
                out_path, fourcc, write_fps, (width, height)
            )
            if writer.isOpened():
                annotated_rel_url = f"/evidence/annotated/{job_id}"
            else:
                writer = None
                logger.warning(
                    "Could not open annotated VideoWriter for job_id=%s",
                    job_id,
                )

        analysis_fps = _get_analysis_fps(fps)
        sample_interval = 1.0 / analysis_fps if analysis_fps > 0 else 0.0
        next_analysis_time = 0.0
        source_frame_number = 0

        update_job(
            job_id,
            status="processing",
            message="Video analysis in progress.",
            started_at=_utc_now(),
            total_frames=total_frames,
            progress=0.0,
            video_info={
                "fps": round(fps, 3),
                "analysis_fps": round(analysis_fps, 3),
                "width": width,
                "height": height,
                "total_frames": total_frames,
            },
            annotated_video_url=annotated_rel_url,
        )

        try:
            import frame_processor
            print(f"[ANALYSIS] Model status: {frame_processor.model_status()}", flush=True)
        except Exception:
            logger.exception("Could not print AI model status")

        print(f"[ANALYSIS] Starting frame loop: {source_label}", flush=True)

        stopped_by_user = False

        while True:
            if _is_cancelled(job_id):
                stopped_by_user = True
                print("[ANALYSIS] Stop requested — closing report for processed frames.", flush=True)
                break

            ok, frame = cap.read()

            if not ok or frame is None:
                print("[ANALYSIS] No more frames. Video reading finished.", flush=True)
                break

            source_frame_number += 1
            source_video_time = (
                (source_frame_number - 1) / fps
                if fps > 0
                else 0.0
            )
            frames_processed += 1

            if frames_processed == 1 or frames_processed % 15 == 0:
                print(
                    f"[ANALYSIS] AI frame {frames_processed}"
                    f" (source frame {source_frame_number}/{total_frames})",
                    flush=True,
                )

            annotated, meta = processor.process_frame(
                frame,
                source_label=source_label,
                draw=True,
            )

            if writer is not None:
                writer.write(annotated)

            last_meta = meta
            # Detection summary
            detections = meta.get(
                "detections",
                [],
            )

            total_detections += len(detections)

            weapon_detections += len(
                meta.get(
                    "weapon_detections",
                    [],
                )
            )

            plate_detections += len(
                meta.get(
                    "ocr_detections",
                    [],
                )
            )
            # Anomaly summary
            anomaly = meta.get(
                "anomaly",
                {},
            )

            if anomaly.get("detected"):
                anomaly_frames += 1

            anomaly_error = float(
                anomaly.get(
                    "error",
                    0.0,
                )
                or 0.0
            )

            max_anomaly_error = max(
                max_anomaly_error,
                anomaly_error,
            )

            # Violence summary
            violence = meta.get(
                "violence",
                {},
            )

            prediction = str(
                violence.get(
                    "prediction",
                    "",
                )
            ).upper()

            confidence = float(
                violence.get(
                    "confidence",
                    0.0,
                )
                or 0.0
            )

            if prediction == "FIGHT":
                fight_frames += 1

            max_violence_confidence = max(
                max_violence_confidence,
                confidence,
            )
            # Timestamped event log (report / timeline / evidence)
            video_time = source_video_time

            def _log_event(event_type, label, confidence_val, extra_fields=None, snap_dets=None):
                if _is_cancelled(job_id):
                    return
                event_id = uuid.uuid4().hex[:12]
                snapshot_url = _save_snapshot(
                    job_id, event_id, frame, detections=snap_dets
                )
                event = {
                    "event_id": event_id,
                    "type": event_type,
                    "label": label,
                    "confidence": round(float(confidence_val), 4),
                    "frame_number": source_frame_number,
                    "analysis_frame_number": frames_processed,
                    "video_time_seconds": round(video_time, 3),
                    "video_timestamp": format_video_timestamp(video_time),
                    "wall_clock_time": _utc_now(),
                    "snapshot_url": snapshot_url,
                }
                if extra_fields:
                    event.update(extra_fields)
                add_event(job_id, event)

            frame_h, frame_w = frame.shape[:2]

            def _snapshot_dets(*extra):
                dets = []
                dets.extend(meta.get("weapon_detections") or [])
                dets.extend(meta.get("ocr_detections") or [])
                dets.extend(meta.get("face_detections") or [])
                for item in extra:
                    if item:
                        dets.append(item)
                return dets

            weapon_dets = meta.get("weapon_detections", [])
            weapon_now = bool(weapon_dets)
            if weapon_now and not active_state["weapon"]:
                top = max(weapon_dets, key=lambda d: d.get("confidence", 0.0))
                bbox = top.get("bbox")
                _log_event(
                    "weapon", top.get("label", "weapon"), top.get("confidence", 0.0),
                    {
                        "detections_in_frame": len(weapon_dets),
                        "bbox": bbox,
                        "location": _describe_location(bbox, frame_w, frame_h, source_label),
                    },
                    snap_dets=_snapshot_dets(),
                )
            active_state["weapon"] = weapon_now

            ocr_dets = meta.get("ocr_detections", [])
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
                should_log_plate = (
                    (not active_state["plate"])
                    or (plate_key != last_plate_key)
                )
                if should_log_plate:
                    bbox = top.get("bbox")
                    _log_event(
                        "plate",
                        plate_text,
                        top.get("confidence", 0.0),
                        {
                            "detections_in_frame": len(ocr_dets),
                            "bbox": bbox,
                            "plate_number": top.get("plate_number"),
                            "ocr_confidence": top.get("ocr_confidence"),
                            "location": _describe_location(
                                bbox, frame_w, frame_h, source_label
                            ),
                        },
                        snap_dets=_snapshot_dets(),
                    )
                    if top.get("plate_number"):
                        plates_seen[plate_key] = {
                            "plate_number": top.get("plate_number"),
                            "confidence": round(
                                float(top.get("confidence", 0.0)), 4
                            ),
                            "ocr_confidence": top.get("ocr_confidence"),
                            "bbox": top.get("bbox"),
                            "frame_number": source_frame_number,
                            "analysis_frame_number": frames_processed,
                            "video_time_seconds": round(video_time, 3),
                        }
                    last_plate_key = plate_key
            else:
                last_plate_key = None
            active_state["plate"] = plate_now

            anomaly_now = bool(anomaly.get("detected"))
            if anomaly_now and not active_state["anomaly"]:
                frame_det = frame_processor.make_frame_level_detection(
                    frame_w, frame_h, "anomaly", "Anomalous activity", confidence=anomaly_error,
                )
                _log_event(
                    "anomaly", "Anomalous activity", 0.0,
                    {
                        "reconstruction_error": round(anomaly_error, 6),
                        "threshold": anomaly.get("threshold"),
                        "bbox": frame_det["bbox"],
                        "location": _describe_location(frame_det["bbox"], frame_w, frame_h, source_label),
                    },
                    snap_dets=_snapshot_dets(frame_det),
                )
            active_state["anomaly"] = anomaly_now

            violence_now = prediction == "FIGHT"
            if violence_now and not active_state["violence"]:
                frame_det = frame_processor.make_frame_level_detection(
                    frame_w, frame_h, "violence", "Fight / violent activity", confidence=confidence,
                )
                _log_event(
                    "violence", "Fight / violent activity", confidence,
                    {
                        "bbox": frame_det["bbox"],
                        "location": _describe_location(frame_det["bbox"], frame_w, frame_h, source_label),
                    },
                    snap_dets=_snapshot_dets(frame_det),
                )
            active_state["violence"] = violence_now

            face_dets = meta.get("face_detections", [])
            face_now = bool(face_dets)
            if face_now:
                top = max(face_dets, key=lambda d: d.get("confidence", 0.0))
                face_key = str(top.get("poi_id") or top.get("label") or "face")
                should_log_face = (not active_state["face"]) or (face_key != last_face_key)
                if should_log_face:
                    bbox = top.get("bbox")
                    _log_event(
                        "face",
                        top.get("label") or "Person of interest",
                        top.get("confidence", 0.0),
                        {
                            "detections_in_frame": len(face_dets),
                            "bbox": bbox,
                            "poi_id": top.get("poi_id"),
                            "face_id": top.get("face_id"),
                            "similarity": top.get("similarity", top.get("confidence")),
                            "location": _describe_location(bbox, frame_w, frame_h, source_label),
                        },
                        snap_dets=_snapshot_dets(),
                    )
                    last_face_key = face_key
            else:
                last_face_key = None
            active_state["face"] = face_now

            # Progress update
            if total_frames:
                progress = min(
                    100.0,
                    (
                        source_frame_number
                        / total_frames
                    )
                    * 100.0,
                )
            else:
                progress = 0.0

            if (
                frames_processed == 1
                or frames_processed % 5 == 0
            ):

                update_job(
                    job_id,
                    frames_processed=frames_processed,
                    progress=round(
                        progress,
                        2,
                    ),
                    message=(
                        f"Analyzing {frames_processed} sampled frames"
                        + (
                            f" (video frame {source_frame_number}/{total_frames})"
                            if total_frames
                            else ""
                        )
                    ),
                )

        elapsed_seconds = (
            time.perf_counter()
            - started
        )

        if frames_processed == 0 and not stopped_by_user:
            raise RuntimeError(
                "Video opened but no frames could be read."
            )

        ocr_meta = (last_meta or {}).get("ocr") or {}
        result = {
            "frames_processed": frames_processed,
            "source_frames_read": source_frame_number,
            "analysis_fps": round(analysis_fps, 3),
            "total_detections": total_detections,
            "weapon_detections": weapon_detections,
            "plate_detections": plate_detections,
            "anomaly_frames": anomaly_frames,
            "fight_frames": fight_frames,
            "max_anomaly_error": round(
                max_anomaly_error,
                6,
            ),
            "max_violence_confidence": round(
                max_violence_confidence,
                4,
            ),
            "processing_seconds": round(
                elapsed_seconds,
                2,
            ),
            "model_status": last_meta.get(
                "model_status"
            ),
            "ocr_text_recognition": bool(
                ocr_meta.get("text_recognition")
            ),
            "plates_found": list(plates_seen.values()),
            "annotated_video_url": annotated_rel_url,
        }
        if stopped_by_user:
            result["ended_by"] = "stop_analysis"

        if out_path and os.path.isfile(out_path):
            try:
                database.add_evidence(
                    job_id, job.get("user_id"), "annotated_video", out_path,
                    os.path.basename(out_path), database.hash_file(out_path)
                )
            except Exception:
                logger.exception("Could not persist annotated video evidence for job_id=%s", job_id)

        completion_message = (
            "Analysis stopped. Report includes detections until Stop was clicked."
            if stopped_by_user
            else "Video analysis completed."
        )

        print(
            f"[ANALYSIS] {'STOPPED' if stopped_by_user else 'COMPLETE'}:"
            f" {frames_processed} AI frames"
            f" sampled from {source_frame_number} source frames in {elapsed_seconds:.2f}s",
            flush=True,
        )

        update_job(
            job_id,
            status="completed",
            message=completion_message,
            progress=100.0,
            frames_processed=frames_processed,
            completed_at=_utc_now(),
            result=result,
            annotated_video_url=annotated_rel_url,
            error=None,
        )

        logger.info(
            "Analysis completed job_id=%s frames=%s seconds=%.2f plates=%s stopped=%s",
            job_id,
            frames_processed,
            elapsed_seconds,
            len(plates_seen),
            stopped_by_user,
        )

    except Exception as exc:
        if _is_cancelled(job_id):
            print(f"[ANALYSIS] Stop during error handling: {exc}", flush=True)
        else:
            print(f"[ANALYSIS] ERROR: {type(exc).__name__}: {exc}", flush=True)
            logger.exception(
                "File analysis failed job_id=%s",
                job_id,
            )
            update_job(
                job_id,
                status="failed",
                message=f"Video analysis failed: {exc}",
                error=str(exc),
                frames_processed=frames_processed,
                completed_at=_utc_now(),
            )

    finally:
        _clear_cancel(job_id)

        if writer is not None:
            try:
                writer.release()
            except Exception:
                logger.exception(
                    "Failed to release annotated video writer"
                )

        if cap is not None:
            try:
                cap.release()
            except Exception:
                logger.exception(
                    "Failed to release video capture"
                )

        with _lock:
            _workers.pop(
                job_id,
                None,
            )

def is_processing(job_id: str) -> bool:
    with _lock:

        worker = _workers.get(job_id)

        return bool(
            worker is not None
            and worker.is_alive()
        )