"""
frame_processor.py
------------------
Single entry point for per-frame AI inference.

Every VideoSource (file, cloud, RTSP, webcam) feeds frames here so
inference is never duplicated per input type.

When combined models are not loaded yet, frames still pass through with
a light overlay so the live dashboard can prove the pipeline path.
Wire real models inside `process_frame` only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("netra.frame_processor")

_lock = threading.Lock()
_models_loaded = False
_weapon_model = None
_load_attempted = False
_stats: Dict[str, Any] = {
    "frames_processed": 0,
    "last_inference_ms": 0.0,
    "model_status": "uninitialized",
    "detections_last_frame": 0,
}


def _repo_root() -> str:
    # backend/frame_processor.py -> repo root
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _try_load_models() -> None:
    """Lazy-load available weights once. Failures are non-fatal."""
    global _models_loaded, _weapon_model, _load_attempted, _stats

    with _lock:
        if _load_attempted:
            return
        _load_attempted = True

        root = _repo_root()
        candidates = [
            os.path.join(root, "models", "weapon.pt"),
            os.path.join(root, "models", "OCR.pt"),
            os.path.join(root, "models", "best.pt"),
        ]
        weight = next((p for p in candidates if os.path.isfile(p)), None)
        if weight is None:
            _stats["model_status"] = "placeholder_no_weights"
            logger.info("No YOLO weights found — using placeholder frame processor.")
            return

        try:
            from ultralytics import YOLO  # type: ignore

            _weapon_model = YOLO(weight)
            _models_loaded = True
            _stats["model_status"] = f"loaded:{os.path.basename(weight)}"
            logger.info("Loaded inference weights: %s", weight)
        except Exception as exc:
            _stats["model_status"] = f"placeholder_load_failed:{exc}"
            logger.warning("Could not load ultralytics model (%s). Using placeholder.", exc)


def model_status() -> str:
    _try_load_models()
    with _lock:
        return str(_stats["model_status"])


def get_stats() -> Dict[str, Any]:
    with _lock:
        return dict(_stats)


def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Run AI inference on one BGR frame.

    Returns (annotated_frame, meta) where meta includes detections and timing.
    This is the ONLY function live / file / cloud monitors should call.
    """
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError("process_frame requires a non-empty numpy BGR frame")

    _try_load_models()
    t0 = time.perf_counter()
    out = frame.copy()
    detections: List[Dict[str, Any]] = []

    if _models_loaded and _weapon_model is not None:
        try:
            results = _weapon_model(out, verbose=False)
            for result in results:
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    conf = float(box.conf[0])
                    if conf < 0.45:
                        continue
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0]) if box.cls is not None else -1
                    name = (
                        result.names.get(cls_id, str(cls_id))
                        if getattr(result, "names", None)
                        else str(cls_id)
                    )
                    detections.append(
                        {
                            "label": name,
                            "confidence": conf,
                            "bbox": [x1, y1, x2, y2],
                        }
                    )
                    if draw:
                        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 220), 2)
                        cv2.putText(
                            out,
                            f"{name} {conf:.2f}",
                            (x1, max(18, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (0, 0, 220),
                            2,
                            cv2.LINE_AA,
                        )
        except Exception:
            logger.exception("Inference failed on frame; returning overlay-only frame")

    if draw:
        _draw_hud(out, source_label=source_label, detections=len(detections))

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    with _lock:
        _stats["frames_processed"] += 1
        _stats["last_inference_ms"] = round(elapsed_ms, 2)
        _stats["detections_last_frame"] = len(detections)
        frames_processed = _stats["frames_processed"]

    meta = {
        "detections": detections,
        "inference_ms": round(elapsed_ms, 2),
        "frames_processed": frames_processed,
        "model_status": model_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_label": source_label,
    }
    return out, meta


def _draw_hud(frame: np.ndarray, *, source_label: str, detections: int) -> None:
    h, w = frame.shape[:2]
    stamp = datetime.now().strftime("%H:%M:%S")
    status = model_status()
    line1 = f"NETRA LIVE  {stamp}"
    line2 = f"{source_label or 'source'}  |  det:{detections}  |  {status}"

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 48), (20, 18, 16), -1)
    cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
    cv2.putText(frame, line1, (12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (63, 232, 208), 1, cv2.LINE_AA)
    cv2.putText(frame, line2[:90], (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 220), 1, cv2.LINE_AA)
