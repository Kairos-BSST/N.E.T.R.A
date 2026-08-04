"""
analysis_pipeline.py
--------------------
Job registry + hand-off into the shared frame AI path.

Every ingested source — local upload, Google Drive / cloud fetch, and
live RTSP / CCTV / webcam — registers here. Continuous frame inference
always goes through frame_processor.process_frame so models are not
duplicated per input type.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("netra.analysis")

# Known source kinds that feed this pipeline.
SOURCE_UPLOAD = "upload"
SOURCE_DRIVE = "drive"
SOURCE_LIVE = "live"
SOURCE_WEBCAM = "webcam"

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def queue_for_analysis(
    *,
    source: str,
    local_path: Optional[str] = None,
    stream_url: Optional[str] = None,
    original_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Enqueue a media item for model analysis.

    Live monitoring updates the job status while frames run through
    process_frame(). File / cloud callers can later open a VideoSource
    and call process_frame() in a loop without changing this registry.
    """
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "source": source,
        "local_path": local_path,
        "stream_url": stream_url,
        "original_name": original_name,
        "status": "queued",
        "message": (
            "Accepted for analysis. Frames are processed via the shared "
            "frame_processor pipeline (live monitoring uses the same path)."
        ),
        "queued_at": datetime.now(timezone.utc).isoformat(),
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
    return dict(job)


def update_job(job_id: Optional[str], **fields: Any) -> Optional[dict]:
    if not job_id:
        return None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(job)


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 50) -> List[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["queued_at"], reverse=True)
        return [dict(j) for j in jobs[:limit]]


def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Public AI entry used by all intake paths.
    Delegates to frame_processor so inference stays in one place.
    """
    import frame_processor

    return frame_processor.process_frame(
        frame, source_label=source_label, draw=draw
    )
