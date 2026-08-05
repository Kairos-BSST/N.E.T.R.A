"""
analysis_pipeline.py
--------------------
Shared analysis job registry and background processing pipeline.

File-based sources such as local uploads and Google Drive downloads are
processed in background threads.

Live CCTV / RTSP / webcam sources are handled continuously by
live_monitor.py but use the same frame_processor.process_frame()
inference entry point.

This keeps all AI inference paths centralized.
"""

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

logger = logging.getLogger("netra.analysis")


# ============================================================
# Source kinds
# ============================================================

SOURCE_UPLOAD = "upload"
SOURCE_DRIVE = "drive"
SOURCE_LIVE = "live"
SOURCE_WEBCAM = "webcam"


# ============================================================
# Job registry
# ============================================================

_lock = threading.RLock()

_jobs: Dict[str, dict] = {}

_workers: Dict[str, threading.Thread] = {}


# ============================================================
# Helpers
# ============================================================

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_job_copy(job: dict) -> dict:
    """
    Return a copy of a job so callers cannot accidentally mutate the
    internal registry.
    """
    return dict(job)


# ============================================================
# Job creation
# ============================================================

def queue_for_analysis(
    *,
    source: str,
    local_path: Optional[str] = None,
    stream_url: Optional[str] = None,
    original_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Register a source for analysis.

    This function only creates the job.

    For file-based sources, call start_file_analysis(job_id) after the
    file has been successfully saved/downloaded.

    Live sources are processed by live_monitor.py.
    """

    job_id = str(uuid.uuid4())

    job = {
        "job_id": job_id,
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
    }

    with _lock:
        _jobs[job_id] = job

    logger.info(
        "Analysis queued job_id=%s source=%s path=%s stream=%s",
        job_id,
        source,
        local_path,
        stream_url,
    )

    return _safe_job_copy(job)


# ============================================================
# Job registry operations
# ============================================================

def update_job(
    job_id: Optional[str],
    **fields: Any,
) -> Optional[dict]:

    if not job_id:
        return None

    with _lock:

        job = _jobs.get(job_id)

        if job is None:
            return None

        job.update(fields)
        job["updated_at"] = _utc_now()

        return _safe_job_copy(job)


def get_job(job_id: str) -> Optional[dict]:

    with _lock:

        job = _jobs.get(job_id)

        if job is None:
            return None

        return _safe_job_copy(job)


def list_jobs(limit: int = 50) -> List[dict]:

    with _lock:

        jobs = sorted(
            _jobs.values(),
            key=lambda item: item["queued_at"],
            reverse=True,
        )

        return [
            _safe_job_copy(job)
            for job in jobs[:limit]
        ]


# ============================================================
# Shared AI entry point
# ============================================================

def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Shared AI entry point.

    All file-based analysis goes through this function.

    Live monitoring uses the same underlying frame_processor module.
    """

    import frame_processor

    return frame_processor.process_frame(
        frame,
        source_label=source_label,
        draw=draw,
    )


# ============================================================
# Background file analysis
# ============================================================

def start_file_analysis(job_id: str) -> dict:
    """
    Start processing a queued local video in a background thread.

    Returns immediately so FastAPI does not block while the entire
    video is analysed.
    """

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

        worker = threading.Thread(
            target=_file_analysis_worker,
            args=(job_id,),
            name=f"netra-analysis-{job_id[:8]}",
            daemon=True,
        )

        _workers[job_id] = worker

        worker.start()

        return _safe_job_copy(job)


def _file_analysis_worker(job_id: str) -> None:
    """
    Worker executed in a daemon thread.

    Opens the video, feeds frames through the shared AI pipeline and
    stores a compact summary in the analysis job.
    """

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

    try:

        cap = cv2.VideoCapture(local_path)

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

        fps = float(
            cap.get(cv2.CAP_PROP_FPS) or 0.0
        )

        width = int(
            cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        )

        height = int(
            cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
        )

        update_job(
            job_id,
            status="processing",
            message="Video analysis in progress.",
            started_at=_utc_now(),
            total_frames=total_frames,
            progress=0.0,
            video_info={
                "fps": round(fps, 3),
                "width": width,
                "height": height,
                "total_frames": total_frames,
            },
        )

        while True:

            ok, frame = cap.read()

            if not ok or frame is None:
                break

            _, meta = process_frame(
                frame,
                source_label=source_label,
                draw=False,
            )

            frames_processed += 1
            last_meta = meta

            # ------------------------------------------------
            # Detection summary
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Anomaly summary
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Violence summary
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Progress update
            # ------------------------------------------------

            if total_frames:
                progress = min(
                    100.0,
                    (
                        frames_processed
                        / total_frames
                    )
                    * 100.0,
                )
            else:
                progress = 0.0

            # Updating the registry every single frame creates
            # unnecessary lock traffic, so update periodically.
            if (
                frames_processed == 1
                or frames_processed % 10 == 0
            ):

                update_job(
                    job_id,
                    frames_processed=frames_processed,
                    progress=round(
                        progress,
                        2,
                    ),
                    message=(
                        f"Processing frame "
                        f"{frames_processed}"
                        + (
                            f"/{total_frames}"
                            if total_frames
                            else ""
                        )
                    ),
                )

        elapsed_seconds = (
            time.perf_counter()
            - started
        )

        if frames_processed == 0:
            raise RuntimeError(
                "Video opened but no frames could be read."
            )

        result = {
            "frames_processed": frames_processed,
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
            "ocr_text_recognition": False,
        }

        update_job(
            job_id,
            status="completed",
            message="Video analysis completed.",
            progress=100.0,
            frames_processed=frames_processed,
            completed_at=_utc_now(),
            result=result,
            error=None,
        )

        logger.info(
            "Analysis completed job_id=%s frames=%s seconds=%.2f",
            job_id,
            frames_processed,
            elapsed_seconds,
        )

    except Exception as exc:

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


# ============================================================
# Worker information
# ============================================================

def is_processing(job_id: str) -> bool:
    """
    Return True if the job currently has an active file-analysis worker.
    """

    with _lock:

        worker = _workers.get(job_id)

        return bool(
            worker is not None
            and worker.is_alive()
        )