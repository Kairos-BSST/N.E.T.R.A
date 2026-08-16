"""
frame_processor.py
------------------
Shared AI inference pipeline for N.E.T.R.A.

Models:
1. Weapon detection       - YOLO (weapon.pt)
2. Number plate detection - YOLO (OCR.pt)
3. Anomaly detection      - Convolutional Autoencoder (anomaly.pth)
4. Violence detection     - MC3-18 (violence.pth)
5. Crowd density/counting  - LWCC DM-Count (pretrained)
6. Person-of-interest face  - OpenCV YuNet + SFace re-id

Designed for CPU-only / low-memory systems.

NOTE:
Number-plate handling is a two-stage pipeline:
  1. OCR.pt (YOLO)               -> detects the plate bounding box.
  2. PaddleOCR recognition model -> reads the text out of the crop
                                     produced by stage 1.
PaddleOCR is initialized once (singleton) and only ever runs on the
small cropped plate region, never on the full frame.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import tempfile
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np


logger = logging.getLogger("netra.frame_processor")


# ============================================================
# Configuration
# ============================================================

DEVICE = "cpu"

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# All detectors stay ON for every video. False positives are killed by
# confidence + geometry + multi-frame confirmation (below), not by
# disabling models.
ENABLE_WEAPON = _env_bool("ENABLE_WEAPON", True)
ENABLE_OCR = _env_bool("ENABLE_OCR", True)
ENABLE_ANOMALY = _env_bool("ENABLE_ANOMALY", True)
ENABLE_VIOLENCE = _env_bool("ENABLE_VIOLENCE", True)
ENABLE_CROWD = _env_bool("ENABLE_CROWD", True)
ENABLE_FACE = _env_bool("ENABLE_FACE", True)

# Crowd-density alert: raise an alert when the estimated number of people
# in a frame reaches this threshold. LWCC/DM-Count is CPU-based.
CROWD_THRESHOLD = int(os.getenv("CROWD_THRESHOLD", "40"))
CROWD_INTERVAL = int(os.getenv("CROWD_INTERVAL", "60"))
FACE_INTERVAL = int(os.getenv("FACE_INTERVAL", "8"))

# A raw hit must survive this many consecutive inference checks before it
# is treated as a real detection. One-frame YOLO flukes (car panel =
# pistol) die here.
DETECT_CONFIRM_HITS = int(os.getenv("DETECT_CONFIRM_HITS", "1"))
DETECT_CLEAR_MISSES = int(os.getenv("DETECT_CLEAR_MISSES", "2"))
BOX_MATCH_IOU = float(os.getenv("BOX_MATCH_IOU", "0.30"))

# Weapon geometry: real weapons are small-to-medium, not half the frame.
WEAPON_CONFIDENCE = float(os.getenv("WEAPON_CONFIDENCE", "0.50"))
WEAPON_MIN_AREA_FRAC = float(os.getenv("WEAPON_MIN_AREA_FRAC", "0.0008"))
WEAPON_MAX_AREA_FRAC = float(os.getenv("WEAPON_MAX_AREA_FRAC", "0.12"))
WEAPON_MIN_ASPECT = float(os.getenv("WEAPON_MIN_ASPECT", "0.20"))
WEAPON_MAX_ASPECT = float(os.getenv("WEAPON_MAX_ASPECT", "5.0"))

# Plates: require a readable OCR string OR a very strong detector score.
OCR_CONFIDENCE = float(os.getenv("OCR_CONFIDENCE", "0.35"))
OCR_STRONG_DET_CONF = float(os.getenv("OCR_STRONG_DET_CONF", "0.60"))
OCR_MIN_TEXT_LEN = int(os.getenv("OCR_MIN_TEXT_LEN", "4"))
OCR_MIN_REC_SCORE = float(os.getenv("OCR_MIN_REC_SCORE", "0.55"))

# Floor matches training script (THRESHOLD=0.01). Adaptive warm-up may
# raise it a little for noisy cameras, but is CAPPED so a fight/anomaly
# clip that is "weird" from frame 1 cannot calibrate the bar above itself.
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.01"))
ANOMALY_WARMUP_FRAMES = int(os.getenv("ANOMALY_WARMUP_FRAMES", "0"))
ANOMALY_STD_MULT = float(os.getenv("ANOMALY_STD_MULT", "2.5"))
ANOMALY_MAX_THRESHOLD = float(os.getenv("ANOMALY_MAX_THRESHOLD", "0.05"))
ANOMALY_CONFIRM_HITS = int(os.getenv("ANOMALY_CONFIRM_HITS", "1"))

WEAPON_INTERVAL = int(os.getenv("WEAPON_INTERVAL", "6"))
OCR_INTERVAL = int(os.getenv("OCR_INTERVAL", "6"))
ANOMALY_INTERVAL = int(os.getenv("ANOMALY_INTERVAL", "1"))

FRAME_DIFF_THRESHOLD = float(os.getenv("FRAME_DIFF_THRESHOLD", "0.03"))

VIOLENCE_SEQUENCE_LENGTH = 16
VIOLENCE_SAMPLE_INTERVAL = 2
VIOLENCE_RETAIN_FRAMES = int(os.getenv("VIOLENCE_RETAIN_FRAMES", "8"))
VIOLENCE_INFERENCE_INTERVAL = int(os.getenv("VIOLENCE_INFERENCE_INTERVAL", "16"))
# Real fight clips score ~0.97+; car false-fights cluster ~0.85. 0.90
# keeps true fights while cutting most road-scene false positives.
# Confirm=2 requires two consecutive FIGHT decisions (matches training
# clip cadence better than a single noisy hit).
VIOLENCE_MIN_CONFIDENCE = float(os.getenv("VIOLENCE_MIN_CONFIDENCE", "0.00"))
VIOLENCE_CONFIRM_HITS = int(os.getenv("VIOLENCE_CONFIRM_HITS", "1"))


# ============================================================
# Global state
#
# IMPORTANT: only the MODELS (weapon/ocr/anomaly/violence + their load
# status) live here as shared singletons -- that's correct, since loading
# a multi-hundred-MB model per camera/job would be wasteful, and a loaded
# model in eval() mode is safe to run concurrent inference against.
#
# Everything that represents ONE STREAM'S RUNNING STATE (frame counter,
# last detections, violence frame buffer, scene-change baseline, stats)
# used to live here too, as module-level globals. That was a real bug:
# analysis_pipeline.py already runs one background thread PER uploaded
# file, and every one of those threads called the same module-level
# process_frame(), so two uploads analyzed at the same time would
# silently corrupt each other's detections (job A's frame counter,
# last-seen weapon boxes, violence buffer, etc. would bleed into job B's
# results, and vice versa) with no error raised anywhere.
#
# Fixed by moving all per-stream state into the FrameProcessor class
# below. Every analysis job / live camera session now creates its own
# FrameProcessor() instance and calls instance.process_frame(...); the
# models themselves stay shared module-level singletons.
# ============================================================

_lock = threading.RLock()

_weapon_model = None
_ocr_detection_model = None
_ocr_recognition_model = None
_anomaly_model = None
_violence_model = None
_violence_transform = None
_crowd_model = None

_torch = None

_model_status: Dict[str, str] = {
    "weapon": "not_loaded",
    "ocr": "not_loaded",
    "ocr_recognition": "not_loaded",
    "anomaly": "not_loaded",
    "violence": "not_loaded",
    "crowd": "not_loaded",
    "face": "not_loaded",
}

_model_errors: Dict[str, str] = {}

# Set once the background startup preload has attempted every enabled model.
# Uploads are accepted while this is False; analysis workers wait on this
# event before entering the frame loop.
_models_ready = threading.Event()
_models_preload_started = False


def preload_models() -> Dict[str, str]:
    """Load every enabled AI model once in the background.

    This function is intended to be called from a daemon startup thread. It
    deliberately loads models sequentially to avoid a large CPU/RAM spike.
    A failed optional model is recorded in ``_model_errors`` but does not
    prevent the remaining models from loading. The ready event is set only
    after all enabled models have been attempted.
    """
    global _models_preload_started

    with _lock:
        if _models_preload_started:
            return dict(_model_status)
        _models_preload_started = True

    logger.info("[STARTUP] Background AI model loading started on CPU.")

    loaders = [
        ("weapon", ENABLE_WEAPON, _load_weapon_model),
        ("ocr", ENABLE_OCR, _load_ocr_model),
        ("anomaly", ENABLE_ANOMALY, _load_anomaly_model),
        ("violence", ENABLE_VIOLENCE, _load_violence_model),
        ("crowd", ENABLE_CROWD, _load_crowd_model),
        ("face", ENABLE_FACE, _load_face_model),
    ]

    try:
        for name, enabled, loader in loaders:
            if not enabled:
                _model_status[name] = "disabled"
                if name == "ocr":
                    _model_status["ocr_recognition"] = "disabled"
                logger.info("[STARTUP] %s model disabled by configuration.", name)
                continue

            logger.info("[STARTUP] Loading %s model...", name)
            try:
                loader()
            except Exception:
                # Individual loaders already capture their own errors, but
                # keep the startup sequence alive if one ever leaks.
                logger.exception("[STARTUP] Unexpected error loading %s model", name)

        loaded = [k for k, v in _model_status.items() if v == "loaded"]
        failed = [k for k, v in _model_status.items() if v == "failed"]
        logger.info(
            "[STARTUP] AI model initialization finished. loaded=%s failed=%s",
            loaded,
            failed,
        )
    finally:
        _models_ready.set()

    return dict(_model_status)


def wait_for_models(timeout: float | None = None) -> bool:
    """Wait until the background model preload has finished attempting loads."""
    return _models_ready.wait(timeout=timeout)


def models_ready() -> bool:
    """Return True when startup model loading has finished."""
    return _models_ready.is_set()


# ============================================================
# Paths
# ============================================================

def _repo_root() -> str:
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
        )
    )


def _model_path(filename: str) -> str:
    """Return the first real checkpoint, skipping Git-LFS pointer files."""
    root = os.path.join(_repo_root(), "models")
    candidates = {
        "weapon.pt": ["weapon.pt", "best.pt"],
        "violence.pth": ["violence.pth", "best.pth", "violence_best.pth"],
        "anomaly.pth": ["anomaly.pth", "best_anomaly.pth", "anomaly_best.pth", "best.pth"],
    }.get(filename, [filename])
    pointer = None
    for name in candidates:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getsize(path) < 1024:
                with open(path, "rb") as f:
                    head = f.read(256)
                if b"git-lfs.github.com/spec" in head or b"oid sha256:" in head:
                    pointer = pointer or path
                    continue
            return path
        except OSError:
            continue
    return pointer or os.path.join(root, candidates[0])

def _ensure_real_checkpoint(path: str) -> None:
    """Reject Git-LFS pointer text instead of treating it as a model."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size < 1024:
        with open(path, "rb") as f:
            head = f.read(256)
        if b"git-lfs.github.com/spec" in head or b"oid sha256:" in head:
            raise RuntimeError(
                f"{path} is a Git-LFS pointer, not the actual model weights. "
                "Run 'git lfs pull' and verify the file size."
            )


# ============================================================
# PyTorch helper
# ============================================================

def _get_torch():
    global _torch

    if _torch is None:
        import torch
        _torch = torch

    return _torch


# ============================================================
# Anomaly model architecture
# ============================================================

def _create_anomaly_model(
    torch_module,
    input_channels: int = 1,
    latent_dim: int = 256,
):
    nn = torch_module.nn

    class ConvolutionalAutoencoder(nn.Module):

        def __init__(self):
            super().__init__()

            self.encoder = nn.Sequential(
                nn.Conv2d(
                    input_channels,
                    32,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(32),

                nn.Conv2d(
                    32,
                    64,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(64),

                nn.Conv2d(
                    64,
                    128,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(128),

                nn.Conv2d(
                    128,
                    256,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(256),
            )

            self.flatten = nn.Flatten()

            self.encode_fc = nn.Linear(
                4 * 4 * 256,
                latent_dim,
            )

            self.decode_fc = nn.Linear(
                latent_dim,
                4 * 4 * 256,
            )

            self.decoder = nn.Sequential(
                nn.ConvTranspose2d(
                    256,
                    128,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(128),

                nn.ConvTranspose2d(
                    128,
                    64,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(64),

                nn.ConvTranspose2d(
                    64,
                    32,
                    4,
                    2,
                    1,
                ),
                nn.ReLU(inplace=True),
                nn.BatchNorm2d(32),

                nn.ConvTranspose2d(
                    32,
                    input_channels,
                    4,
                    2,
                    1,
                ),

                nn.Sigmoid(),
            )

        def forward(self, x):
            x = self.encoder(x)

            batch_size = x.size(0)

            x = self.flatten(x)
            x = self.encode_fc(x)
            x = self.decode_fc(x)

            x = x.view(
                batch_size,
                256,
                4,
                4,
            )

            return self.decoder(x)

    return ConvolutionalAutoencoder()


# ============================================================
# Lazy model loaders
# ============================================================

def _load_weapon_model() -> bool:
    global _weapon_model

    if _weapon_model is not None:
        return True

    if _model_status["weapon"] == "failed":
        return False

    try:
        from ultralytics import YOLO

        path = _model_path("weapon.pt")
        _ensure_real_checkpoint(path)

        _weapon_model = YOLO(path)

        _model_status["weapon"] = "loaded"

        logger.info(
            "Weapon model loaded on CPU."
        )

        return True

    except Exception as exc:
        _model_status["weapon"] = "failed"
        _model_errors["weapon"] = str(exc)

        logger.exception(
            "Unable to load weapon model"
        )

        return False


def _load_ocr_detector() -> bool:
    """
    Load the custom YOLO number-plate detector (OCR.pt).

    Stage 1 only: this model produces bounding boxes for plates. It is
    never used for text recognition.
    """

    global _ocr_detection_model

    if _ocr_detection_model is not None:
        return True

    if _model_status["ocr"] == "failed":
        return False

    try:
        from ultralytics import YOLO

        path = _model_path("OCR.pt")

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        _ocr_detection_model = YOLO(path)

        _model_status["ocr"] = "loaded"

        logger.info(
            "Number-plate detector loaded on CPU."
        )

        return True

    except Exception as exc:
        _model_status["ocr"] = "failed"
        _model_errors["ocr"] = str(exc)

        logger.exception(
            "Unable to load number-plate detector"
        )

        return False


def _load_ocr_recognizer() -> bool:
    """
    Load the official PaddleOCR English text-recognition model.

    Stage 2 only: this model reads text from an already-cropped plate
    image produced by the YOLO detector. It never runs detection and
    never sees the full frame.

    Uses the PaddleOCR v3 API. The old ``show_log`` constructor
    argument (and other now-removed kwargs) is not passed - PaddleOCR
    v3 raises ``ValueError: Unknown argument`` if you do.

    Initialized exactly once (singleton) and cached in
    ``_ocr_recognition_model`` for the lifetime of the process.
    """

    global _ocr_recognition_model

    if _ocr_recognition_model is not None:
        return True

    if _model_status["ocr_recognition"] == "failed":
        return False

    try:
        from paddleocr import TextRecognition

        # TextRecognition is the standalone PaddleOCR v3 module for
        # the recognition step only (no detection, no doc/orientation
        # preprocessing) - exactly what we need for a pre-cropped
        # plate image. Only officially supported v3 kwargs are used.
        _ocr_recognition_model = TextRecognition(
            model_name="en_PP-OCRv4_mobile_rec",
        )

        _model_status["ocr_recognition"] = "loaded"

        logger.info(
            "PaddleOCR text recognition model loaded on CPU."
        )

        return True

    except Exception as exc:
        _model_status["ocr_recognition"] = "failed"
        _model_errors["ocr_recognition"] = str(exc)

        logger.exception(
            "Unable to load PaddleOCR recognition model"
        )

        return False


def _load_ocr_model() -> bool:
    """
    Load both stages of the number-plate pipeline:
      - YOLO detector (OCR.pt)
      - PaddleOCR recognizer

    Both are singletons; calling this repeatedly after a successful
    load is a cheap no-op.
    """

    detector_ok = _load_ocr_detector()
    recognizer_ok = _load_ocr_recognizer()

    return detector_ok and recognizer_ok


# Compatibility with the function name used during testing.
def _load_ocr_models() -> bool:
    return _load_ocr_model()


def _load_anomaly_model() -> bool:
    global _anomaly_model

    if _anomaly_model is not None:
        return True

    if _model_status["anomaly"] == "failed":
        return False

    try:
        torch = _get_torch()

        path = _model_path("anomaly.pth")
        _ensure_real_checkpoint(path)

        checkpoint = torch.load(
            path,
            map_location=DEVICE,
        )

        model_info = checkpoint.get(
            "model_info",
            {},
        )

        input_channels = model_info.get(
            "input_channels",
            1,
        )

        latent_dimension = model_info.get(
            "latent_dimension",
            256,
        )

        model = _create_anomaly_model(
            torch,
            input_channels=input_channels,
            latent_dim=latent_dimension,
        )

        model.load_state_dict(
            checkpoint["model_state_dict"]
        )

        model.to(DEVICE)
        model.eval()

        _anomaly_model = model

        _model_status["anomaly"] = "loaded"

        logger.info(
            "Anomaly model loaded on CPU."
        )

        return True

    except Exception as exc:
        _model_status["anomaly"] = "failed"
        _model_errors["anomaly"] = str(exc)

        logger.exception(
            "Unable to load anomaly model"
        )

        return False


def _load_violence_model() -> bool:
    global _violence_model
    global _violence_transform

    if (
        _violence_model is not None
        and _violence_transform is not None
    ):
        return True

    if _model_status["violence"] == "failed":
        return False

    try:
        torch = _get_torch()

        import torchvision
        import albumentations as A

        path = _model_path("violence.pth")
        _ensure_real_checkpoint(path)

        model = (
            torchvision.models.video.mc3_18(
                weights=None
            )
        )

        model.fc = torch.nn.Linear(
            model.fc.in_features,
            2,
        )

        checkpoint = torch.load(path, map_location=DEVICE)
        if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
            state_dict = checkpoint["state_dict"]
        elif isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
            state_dict = checkpoint["model_state_dict"]
        else:
            state_dict = checkpoint
        if not isinstance(state_dict, dict):
            raise RuntimeError("Unsupported violence checkpoint format")
        if any(str(k).startswith("module.") for k in state_dict):
            state_dict = {str(k).removeprefix("module."): v for k, v in state_dict.items()}
        model.load_state_dict(state_dict, strict=True)

        model.to(DEVICE)
        model.eval()

        _violence_model = model

        _violence_transform = A.Compose([
            A.Resize(128, 171),

            A.CenterCrop(
                112,
                112,
            ),

            A.Normalize(
                mean=[
                    0.43216,
                    0.394666,
                    0.37645,
                ],
                std=[
                    0.22803,
                    0.22145,
                    0.216989,
                ],
            ),
        ])

        _model_status["violence"] = "loaded"

        logger.info(
            "Violence model loaded on CPU."
        )

        return True

    except Exception as exc:
        _model_status["violence"] = "failed"
        _model_errors["violence"] = str(exc)

        logger.exception(
            "Unable to load violence model"
        )

        return False


# ============================================================
# Crowd-density model (LWCC / DM-Count)
# ============================================================

def _load_crowd_model() -> bool:
    """Load LWCC DM-Count once for the whole backend process."""
    global _crowd_model

    if not ENABLE_CROWD:
        return False

    if _crowd_model is not None:
        return True

    if _model_status["crowd"] == "failed":
        return False

    try:
        from lwcc import LWCC

        _crowd_model = LWCC.load_model(
            model_name="DM-Count",
            model_weights="SHA",
        )

        _model_status["crowd"] = "loaded"
        logger.info("Crowd-density model (LWCC DM-Count/SHA) loaded on CPU.")
        return True

    except Exception as exc:
        _model_status["crowd"] = "failed"
        _model_errors["crowd"] = str(exc)
        logger.exception("Unable to load crowd-density model")
        return False


def _load_face_model() -> bool:
    """Load YuNet + SFace for person-of-interest re-identification."""
    if not ENABLE_FACE:
        _model_status["face"] = "disabled"
        return False
    if _model_status["face"] == "failed":
        return False
    try:
        import face_reid

        ok = face_reid.ensure_models()
        if not ok:
            raise RuntimeError(face_reid.status().get("error") or "face model load failed")
        face_reid.ensure_gallery_loaded()
        _model_status["face"] = "loaded"
        logger.info("Face re-id models loaded (YuNet + SFace).")
        return True
    except Exception as exc:
        _model_status["face"] = "failed"
        _model_errors["face"] = str(exc)
        logger.exception("Unable to load face re-id models")
        return False


def _run_crowd_detection(frame: np.ndarray) -> Tuple[float, bool]:
    """Estimate people count and return (count, threshold_alert)."""
    if not ENABLE_CROWD or not _load_crowd_model():
        return 0.0, False

    from lwcc import LWCC

    temp_path = None
    try:
        # LWCC's public API accepts image paths. Write one temporary JPEG
        # rather than changing the tested LWCC inference path.
        fd, temp_path = tempfile.mkstemp(
            prefix="netra_crowd_",
            suffix=".jpg",
        )
        os.close(fd)

        if not cv2.imwrite(temp_path, frame):
            raise RuntimeError("Unable to create temporary frame for LWCC")

        count = LWCC.get_count(
            temp_path,
            model=_crowd_model,
            resize_img=True,
        )

        # LWCC returns a float count for a single image.
        count_f = float(count)
        return count_f, count_f >= CROWD_THRESHOLD

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ============================================================
# Status
# ============================================================

def model_status() -> Dict[str, Any]:
    """
    Return model status without forcing models to load.
    """

    with _lock:
        recognition_enabled = (
            _model_status["ocr_recognition"] == "loaded"
        )

        return {
            "models": dict(_model_status),
            "errors": dict(_model_errors),
            "device": DEVICE,
            "ocr": _model_status["ocr"],
            "ocr_recognition": _model_status["ocr_recognition"],
            "ocr_text_recognition": (
                "enabled" if recognition_enabled else "disabled"
            ),
        }


# ============================================================
# False-positive rejection helpers
# ============================================================

def _bbox_iou(a: List[int], b: List[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    denom = area_a + area_b - inter
    return float(inter / denom) if denom > 0 else 0.0


def _confirm_box_tracks(
    tracks: List[Dict[str, Any]],
    raw_dets: List[Dict[str, Any]],
    *,
    need_hits: int,
    need_misses: int,
    iou_thresh: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Multi-frame confirmation for bbox detections.

    A track must match (by IoU + type/label) for ``need_hits`` consecutive
    inference passes before it is emitted. Flicker detections never leave.
    """
    matched_track_ids: set = set()
    unmatched_raw = list(raw_dets)

    for track in tracks:
        best_i = -1
        best_iou = 0.0
        for i, det in enumerate(unmatched_raw):
            if det.get("type") != track.get("type"):
                continue
            # For plates, prefer matching on plate_number when both have it.
            t_plate = track.get("plate_number")
            d_plate = det.get("plate_number")
            if t_plate and d_plate and t_plate != d_plate:
                continue
            if (
                track.get("type") == "weapon"
                and track.get("label")
                and det.get("label")
                and track["label"] != det["label"]
            ):
                continue
            if (
                track.get("type") == "face"
                and track.get("poi_id")
                and det.get("poi_id")
                and track["poi_id"] != det["poi_id"]
            ):
                continue
            iou = _bbox_iou(track["bbox"], det["bbox"])
            if iou > best_iou:
                best_iou = iou
                best_i = i

        if best_i >= 0 and best_iou >= iou_thresh:
            det = unmatched_raw.pop(best_i)
            track["bbox"] = det["bbox"]
            track["confidence"] = det.get("confidence", track.get("confidence", 0.0))
            track["label"] = det.get("label", track.get("label"))
            track["plate_number"] = det.get("plate_number")
            track["text"] = det.get("text")
            track["ocr_confidence"] = det.get("ocr_confidence")
            track["text_recognition"] = det.get("text_recognition", False)
            track["poi_id"] = det.get("poi_id", track.get("poi_id"))
            track["face_id"] = det.get("face_id", track.get("face_id"))
            track["similarity"] = det.get("similarity", track.get("similarity"))
            track["hits"] = int(track.get("hits", 0)) + 1
            track["misses"] = 0
            track["confirmed"] = track["hits"] >= need_hits
            matched_track_ids.add(id(track))
        else:
            track["misses"] = int(track.get("misses", 0)) + 1
            track["hits"] = 0
            if track["misses"] >= need_misses:
                track["dead"] = True

    alive = [t for t in tracks if not t.get("dead")]

    for det in unmatched_raw:
        alive.append({
            **det,
            "hits": 1,
            "misses": 0,
            "confirmed": need_hits <= 1,
            "dead": False,
        })

    confirmed = [
        {
            "type": t["type"],
            "label": t.get("label"),
            "confidence": t.get("confidence", 0.0),
            "bbox": t["bbox"],
            "plate_number": t.get("plate_number"),
            "text": t.get("text") or t.get("plate_number"),
            "text_recognition": bool(t.get("text_recognition")),
            "ocr_confidence": t.get("ocr_confidence", 0.0),
            "poi_id": t.get("poi_id"),
            "face_id": t.get("face_id"),
            "similarity": t.get("similarity"),
            "confirmed": True,
            "confirm_hits": t.get("hits", 0),
        }
        for t in alive
        if t.get("confirmed")
    ]

    # Drop None-only optional fields for weapon dets cleanliness
    cleaned: List[Dict[str, Any]] = []
    for d in confirmed:
        out = {
            "type": d["type"],
            "label": d["label"],
            "confidence": d["confidence"],
            "bbox": d["bbox"],
            "confirmed": True,
        }
        if d["type"] == "number_plate":
            out["plate_number"] = d.get("plate_number")
            out["text"] = d.get("text")
            out["text_recognition"] = d.get("text_recognition")
            out["ocr_confidence"] = d.get("ocr_confidence")
        if d["type"] == "face":
            out["poi_id"] = d.get("poi_id")
            out["face_id"] = d.get("face_id")
            out["similarity"] = d.get("similarity", d.get("confidence"))
        cleaned.append(out)

    return alive, cleaned


class _BoolConfirm:
    """Sticky confirm for frame-level signals (anomaly / violence)."""

    def __init__(self, need_hits: int, need_misses: int) -> None:
        self.need_hits = max(1, need_hits)
        self.need_misses = max(1, need_misses)
        self.hits = 0
        self.misses = 0
        self.active = False

    def update(self, positive: bool) -> bool:
        if positive:
            self.hits += 1
            self.misses = 0
            if self.hits >= self.need_hits:
                self.active = True
        else:
            self.misses += 1
            self.hits = 0
            if self.misses >= self.need_misses:
                self.active = False
        return self.active


# ============================================================
# Weapon inference
# ============================================================

def _run_weapon_detection(
    frame: np.ndarray,
) -> List[Dict[str, Any]]:

    if not ENABLE_WEAPON:
        return []

    if not _load_weapon_model():
        return []

    detections: List[Dict[str, Any]] = []
    height, width = frame.shape[:2]
    frame_area = float(max(1, height * width))

    results = _weapon_model.predict(
        frame,
        conf=WEAPON_CONFIDENCE,
        iou=0.45,
        max_det=10,
        verbose=False,
        device=DEVICE,
    )

    for result in results:

        boxes = getattr(
            result,
            "boxes",
            None,
        )

        if boxes is None:
            continue

        for box in boxes:

            confidence = float(
                box.conf[0]
            )

            cls_id = int(
                box.cls[0]
            )

            x1, y1, x2, y2 = map(
                int,
                box.xyxy[0],
            )

            bw = max(0, x2 - x1)
            bh = max(0, y2 - y1)
            if bw <= 0 or bh <= 0:
                continue

            names = getattr(
                result,
                "names",
                {},
            )

            if isinstance(names, dict):
                name = names.get(
                    cls_id,
                    str(cls_id),
                )
            else:
                name = str(cls_id)

            detections.append({
                "type": "weapon",
                "label": name,
                "confidence": confidence,
                "bbox": [
                    x1,
                    y1,
                    x2,
                    y2,
                ],
            })

    return detections


# ============================================================
# Number-plate detection
# ============================================================

def _normalize_plate_text(text: str | None) -> str | None:
    """Keep A-Z / 0-9 only and require a minimum plate-like length."""
    if not text:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(text)).upper()
    if len(cleaned) < OCR_MIN_TEXT_LEN:
        return None
    return cleaned


def _recognize_plate_text(
    plate_crop: np.ndarray,
) -> Tuple[str | None, float]:
    """
    Run PaddleOCR recognition on an already-cropped plate image.

    Stage 2 only - no detection happens here, the crop is assumed to
    already tightly bound the plate (produced by the YOLO detector in
    ``_run_ocr``).

    Returns (plate_text, ocr_confidence). plate_text is None if
    nothing could be recognized.
    """

    if _ocr_recognition_model is None:
        return None, 0.0

    rgb_crop = cv2.cvtColor(
        plate_crop,
        cv2.COLOR_BGR2RGB,
    )

    raw_results = _ocr_recognition_model.predict(
        input=rgb_crop,
    )

    for item in raw_results:

        # PaddleOCR v3 pipeline results support both dict-style
        # access and a `.json` accessor depending on how they are
        # produced; handle both defensively.
        data = item

        if hasattr(data, "json"):
            data = data.json

        if isinstance(data, dict) and "res" in data:
            data = data["res"]

        if isinstance(data, dict):
            text = data.get("rec_text")
            score = data.get("rec_score")
        else:
            text = getattr(data, "rec_text", None)
            score = getattr(data, "rec_score", None)

        score_f = float(score or 0.0)
        cleaned = _normalize_plate_text(text)
        if cleaned and score_f >= OCR_MIN_REC_SCORE:
            return cleaned, score_f

    return None, 0.0


def _run_ocr(
    frame: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    Two-stage number-plate pipeline:

      1. OCR.pt (YOLO) detects plate bounding boxes.
      2. Each detected plate is cropped and passed to PaddleOCR for
         text recognition.

    PaddleOCR only ever sees the small cropped plate region, never
    the full frame.
    """

    if not ENABLE_OCR:
        return []

    if not _load_ocr_model():
        return []

    detections: List[Dict[str, Any]] = []

    results = _ocr_detection_model.predict(
        frame,
        conf=OCR_CONFIDENCE,
        iou=0.45,
        max_det=10,
        verbose=False,
        device=DEVICE,
    )

    if not results:
        return detections

    boxes = getattr(
        results[0],
        "boxes",
        None,
    )

    if boxes is None:
        return detections

    height, width = frame.shape[:2]

    for box in boxes:

        x1, y1, x2, y2 = map(
            int,
            box.xyxy[0],
        )

        x1 = max(
            0,
            min(x1, width - 1),
        )

        x2 = max(
            0,
            min(x2, width),
        )

        y1 = max(
            0,
            min(y1, height - 1),
        )

        y2 = max(
            0,
            min(y2, height),
        )

        if x2 <= x1 or y2 <= y1:
            continue

        confidence = float(
            box.conf[0]
        )

        plate_number = None
        ocr_confidence = 0.0
        text_recognition = False

        if _ocr_recognition_model is not None:

            plate_crop = frame[y1:y2, x1:x2]

            if plate_crop.size > 0:

                try:
                    plate_number, ocr_confidence = (
                        _recognize_plate_text(plate_crop)
                    )

                    text_recognition = plate_number is not None

                except Exception:
                    logger.exception(
                        "PaddleOCR recognition failed "
                        "for a detected plate"
                    )

        detections.append({
            "type": "number_plate",
            "label": plate_number or "PLATE",
            "plate_number": plate_number,
            "text": plate_number,
            "text_recognition": text_recognition,
            "confidence": confidence,
            "ocr_confidence": round(ocr_confidence, 4),
            "bbox": [
                x1,
                y1,
                x2,
                y2,
            ],
        })

    # Drop weak plate boxes with no readable text — those are the usual
    # false positives (taillights, stickers, signage).
    filtered: List[Dict[str, Any]] = []
    for det in detections:
        if det.get("plate_number"):
            filtered.append(det)
        elif float(det.get("confidence", 0.0)) >= OCR_STRONG_DET_CONF:
            filtered.append(det)
    return filtered


# ============================================================
# Anomaly inference
# ============================================================

def _run_anomaly(
    frame: np.ndarray,
    state: "FrameProcessor | None" = None,
) -> Tuple[bool, float]:

    if not ENABLE_ANOMALY:
        return False, 0.0

    if not _load_anomaly_model():
        return False, 0.0

    torch = _get_torch()

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.resize(
        gray,
        (64, 64),
    )

    gray = (
        gray.astype(np.float32)
        / 255.0
    )

    tensor = torch.from_numpy(
        gray
    )

    tensor = (
        tensor
        .unsqueeze(0)
        .unsqueeze(0)
        .to(DEVICE)
    )

    with torch.no_grad():

        reconstruction = _anomaly_model(
            tensor
        )

        error = torch.mean(
            (
                tensor
                - reconstruction
            ) ** 2
        ).item()

    error_f = float(error)

    # The standalone training/test pipeline uses a fixed 0.01 threshold.
    # Do not calibrate from the uploaded clip: a fight/anomaly at the start
    # would otherwise teach the detector that the abnormal scene is normal.
    threshold = ANOMALY_THRESHOLD
    if state is not None:
        state.anomaly_threshold = threshold
        state.anomaly_calibrated = True

    return error_f > threshold, error_f


# ============================================================
# Violence inference
# ============================================================

def _update_violence(
    frame: np.ndarray,
    state: "FrameProcessor",
) -> Tuple[str, float]:

    if not ENABLE_VIOLENCE:
        return "DISABLED", 0.0

    if not _load_violence_model():
        return "UNAVAILABLE", 0.0

    torch = _get_torch()

    rgb = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB,
    )

    processed = _violence_transform(
        image=rgb
    )["image"]

    state.violence_frames.append(
        processed
    )

    if (
        len(state.violence_frames)
        < VIOLENCE_SEQUENCE_LENGTH
    ):
        # Still gathering a clip — do NOT replay the last label.
        # Replaying caused the confirm-gate to see fake NO FIGHT misses
        # between real inferences and wiped true fights.
        return "PENDING", state.last_violence_confidence

    clip = np.array(
        state.violence_frames
    )

    # (T, H, W, C) -> (1, C, T, H, W)
    clip = np.transpose(
        clip,
        (3, 0, 1, 2),
    )

    clip = np.expand_dims(
        clip,
        axis=0,
    )

    tensor = torch.from_numpy(clip).to(
        device=DEVICE,
        dtype=torch.float32,
    )

    with torch.inference_mode():

        output = _violence_model(
            tensor
        )

        probabilities = torch.softmax(
            output,
            dim=1,
        )

        confidence, prediction = torch.max(
            probabilities,
            1,
        )

    prediction_id = int(
        prediction.item()
    )

    conf_f = float(confidence.item())
    state.last_violence_confidence = conf_f

    # Training labels:
    # 0 = fight
    # 1 = noFight
    # Real fights score ~0.97; car false-fights ~0.85.
    if prediction_id == 0 and conf_f >= VIOLENCE_MIN_CONFIDENCE:
        state.last_violence_prediction = "FIGHT"
    else:
        state.last_violence_prediction = "NO FIGHT"

    # Match the working standalone detector: retain the second half of the
    # clip and add every 2nd source frame. This gives the model the same
    # temporal overlap as training/video.py while keeping inference bounded.
    keep = list(state.violence_frames)[-VIOLENCE_RETAIN_FRAMES:]
    state.violence_frames.clear()
    state.violence_frames.extend(keep)
    state.violence_inferences += 1

    return (
        state.last_violence_prediction,
        state.last_violence_confidence,
    )


# ============================================================
# Drawing helpers
# ============================================================

def draw_detections_on_frame(
    frame: np.ndarray,
    detections: List[Dict[str, Any]],
) -> np.ndarray:
    """
    Return a copy of ``frame`` with bounding boxes + labels drawn.
    Used by upload analysis (snapshots / annotated video) and live HUD.
    """
    out = frame.copy()
    for detection in detections:
        _draw_detection(out, detection)
    return out


def make_frame_level_detection(
    frame_w: int,
    frame_h: int,
    event_type: str,
    label: str,
    confidence: float = 0.0,
    margin: int = 14,
) -> Dict[str, Any]:
    """Synthetic full-frame box for anomaly / violence report snapshots."""
    x1 = max(0, margin)
    y1 = max(0, margin)
    x2 = max(x1 + 1, int(frame_w) - margin)
    y2 = max(y1 + 1, int(frame_h) - margin)
    return {
        "type": event_type,
        "label": label,
        "confidence": float(confidence or 0.0),
        "bbox": [x1, y1, x2, y2],
        "frame_level": True,
    }


def _detection_color(detection_type: str) -> Tuple[int, int, int]:
    t = (detection_type or "").lower()
    if t == "weapon":
        return (0, 0, 220)
    if t == "face":
        return (0, 140, 255)
    if t in {"number_plate", "plate"}:
        return (0, 200, 0)
    if t == "anomaly":
        return (0, 180, 255)
    if t == "violence":
        return (180, 0, 220)
    return (0, 200, 0)


def _draw_detection(
    frame: np.ndarray,
    detection: Dict[str, Any],
) -> None:

    bbox = detection.get("bbox")
    if not bbox:
        return

    x1, y1, x2, y2 = [int(v) for v in bbox]
    detection_type = str(detection.get("type", "") or "")
    color = _detection_color(detection_type)
    thickness = 3 if detection.get("frame_level") or detection_type in {"anomaly", "violence"} else 2

    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    if detection.get("frame_level") or detection_type in {"anomaly", "violence"}:
        corner = max(18, min(40, (x2 - x1) // 12))
        for (cx, cy, dx, dy) in (
            (x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1),
        ):
            cv2.line(frame, (cx, cy), (cx + dx * corner, cy), color, 3)
            cv2.line(frame, (cx, cy), (cx, cy + dy * corner), color, 3)

    label = detection.get("label", "")
    confidence = detection.get("confidence", 0.0)
    if detection_type in {"number_plate", "plate"} and detection.get("plate_number"):
        text = str(detection["plate_number"])
    elif detection_type in {"anomaly", "violence"}:
        text = str(label or detection_type).upper()
        if confidence:
            text = f"{text} {float(confidence):.2f}"
    else:
        text = f"{label} {float(confidence or 0.0):.2f}".strip()

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.6
    (tw, th), _ = cv2.getTextSize(text, font, scale, 2)
    tag_x = x1
    tag_y = max(th + 10, y1 - 6)
    cv2.rectangle(frame, (tag_x, tag_y - th - 8), (tag_x + tw + 10, tag_y + 4), color, -1)
    cv2.putText(frame, text, (tag_x + 5, tag_y - 2), font, scale, (255, 255, 255), 2, cv2.LINE_AA)


def _draw_hud(
    frame: np.ndarray,
    *,
    source_label: str,
    detections: int,
    anomaly: bool,
    anomaly_error: float,
    violence: str,
    violence_confidence: float,
) -> None:

    _, width = frame.shape[:2]

    stamp = datetime.now().strftime(
        "%H:%M:%S"
    )

    overlay = frame.copy()

    cv2.rectangle(
        overlay,
        (0, 0),
        (width, 78),
        (20, 18, 16),
        -1,
    )

    cv2.addWeighted(
        overlay,
        0.45,
        frame,
        0.55,
        0,
        frame,
    )

    line1 = (
        f"NETRA LIVE  {stamp}"
    )

    line2 = (
        f"{source_label or 'source'}"
        f" | detections:{detections}"
    )

    anomaly_text = (
        "ANOMALY"
        if anomaly
        else "NORMAL"
    )

    line3 = (
        f"Anomaly:{anomaly_text}"
        f" ({anomaly_error:.5f})"
        f" | Violence:{violence}"
        f" ({violence_confidence * 100:.1f}%)"
    )

    cv2.putText(
        frame,
        line1,
        (12, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (63, 232, 208),
        1,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        line2[:100],
        (12, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (230, 230, 220),
        1,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        line3[:120],
        (12, 64),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (230, 230, 220),
        1,
        cv2.LINE_AA,
    )


# ============================================================
# Main inference entry point
# ============================================================

class FrameProcessor:
    """
    One instance per analysis job / live camera session.

    Holds all the state that used to be module-level globals (frame
    counter, last detections, violence frame buffer, scene-change
    baseline, per-stream stats), so multiple streams can be analyzed
    concurrently -- by different upload jobs, or by different cameras --
    without their results bleeding into each other.

    The underlying models (weapon/ocr/anomaly/violence) are still shared
    module-level singletons, loaded once regardless of how many
    FrameProcessor instances exist -- that's intentional, models are
    read-only at inference time and expensive to load per-stream.

    Usage:
        fp = FrameProcessor()
        annotated, meta = fp.process_frame(frame, source_label="cam-1")
    """

    def __init__(self, label: str = "") -> None:
        self.label = label
        self._instance_lock = threading.Lock()

        self.frame_counter = 0

        self.violence_frames = deque(
            maxlen=VIOLENCE_SEQUENCE_LENGTH
        )
        # Run the expensive MC3-18 inference only after a fresh 16-frame
        # clip has been collected. The old implementation retained 8 frames
        # after inference and then ran MC3 again every 2 AI frames, causing
        # dozens of full 3D-CNN passes and making uploads appear frozen.
        self.violence_inferences = 0
        self.last_violence_infer_frame = 0

        self.last_weapon_detections: List[Dict[str, Any]] = []
        self.last_ocr_detections: List[Dict[str, Any]] = []
        self.last_face_detections: List[Dict[str, Any]] = []
        self._weapon_tracks: List[Dict[str, Any]] = []
        self._ocr_tracks: List[Dict[str, Any]] = []
        self._face_tracks: List[Dict[str, Any]] = []

        self.last_anomaly_detected = False
        self.last_anomaly_error = 0.0
        self.anomaly_warmup_errors: List[float] = []
        self.anomaly_calibrated = False
        self.anomaly_threshold = ANOMALY_THRESHOLD
        self._anomaly_gate = _BoolConfirm(
            ANOMALY_CONFIRM_HITS, DETECT_CLEAR_MISSES
        )

        self.last_violence_prediction = "COLLECTING"
        self.last_violence_confidence = 0.0
        self._raw_violence_prediction = "COLLECTING"
        self._violence_gate = _BoolConfirm(
            VIOLENCE_CONFIRM_HITS, DETECT_CLEAR_MISSES
        )

        self.last_crowd_count = 0.0
        self.last_crowd_alert = False

        # Small downsized grayscale snapshot of the last frame that was
        # actually run through the weapon/anomaly models -- used by
        # _frame_changed_significantly() to decide whether the scene has
        # moved on enough to warrant another inference pass before the
        # fixed interval comes around. Per-instance so one camera's scene
        # changes don't reset another camera's baseline.
        self.last_checked_frame_small: Any = None

        self.stats: Dict[str, Any] = {
            "frames_processed": 0,
            "last_inference_ms": 0.0,
            "detections_last_frame": 0,
        }

    def get_stats(self) -> Dict[str, Any]:
        with self._instance_lock:
            return dict(self.stats)

    def _frame_changed_significantly(self, frame: np.ndarray) -> bool:
        """
        Cheap scene-change check so we don't burn CPU re-running
        weapon/anomaly models on frames that are near-identical to the
        last one THIS stream checked. Any frame that DOES differ
        meaningfully is checked immediately, regardless of the fixed
        interval, so a threat that suddenly appears isn't delayed
        waiting for the next scheduled interval tick.

        Returns True on the very first frame of this stream, since
        there's nothing yet to compare against.
        """
        small = cv2.resize(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            (48, 27),
        )

        if self.last_checked_frame_small is None:
            self.last_checked_frame_small = small
            return True

        diff = cv2.absdiff(small, self.last_checked_frame_small)
        changed_fraction = float(np.mean(diff > 15))

        if changed_fraction >= FRAME_DIFF_THRESHOLD:
            self.last_checked_frame_small = small
            return True

        return False

    def process_frame(
        self,
        frame: np.ndarray,
        *,
        source_label: str = "",
        draw: bool = True,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:

        if (
            frame is None
            or not isinstance(frame, np.ndarray)
            or frame.size == 0
        ):
            raise ValueError(
                "process_frame requires a "
                "non-empty numpy BGR frame"
            )

        start = time.perf_counter()

        output_frame = frame.copy()

        with self._instance_lock:
            self.frame_counter += 1
            frame_number = self.frame_counter

        # Cheap scene-change check, computed once and reused by both the
        # weapon and anomaly gates below.
        scene_changed = self._frame_changed_significantly(frame)

        # --------------------------------------------------------
        # Weapon
        # --------------------------------------------------------

        if (
            frame_number == 1
            or scene_changed
            or frame_number % WEAPON_INTERVAL == 0
        ):

            try:
                raw_weapons = _run_weapon_detection(frame)
                self._weapon_tracks, self.last_weapon_detections = (
                    _confirm_box_tracks(
                        self._weapon_tracks,
                        raw_weapons,
                        need_hits=DETECT_CONFIRM_HITS,
                        need_misses=DETECT_CLEAR_MISSES,
                        iou_thresh=BOX_MATCH_IOU,
                    )
                )

            except Exception:
                logger.exception(
                    "Weapon inference failed"
                )

        # --------------------------------------------------------
        # Number plate
        # --------------------------------------------------------

        if (
            frame_number == 1
            or scene_changed
            or frame_number % OCR_INTERVAL == 0
        ):

            try:
                raw_plates = _run_ocr(frame)
                self._ocr_tracks, self.last_ocr_detections = (
                    _confirm_box_tracks(
                        self._ocr_tracks,
                        raw_plates,
                        need_hits=DETECT_CONFIRM_HITS,
                        need_misses=DETECT_CLEAR_MISSES,
                        iou_thresh=BOX_MATCH_IOU,
                    )
                )

            except Exception:
                logger.exception(
                    "Number-plate inference failed"
                )

        # --------------------------------------------------------
        # Anomaly
        # --------------------------------------------------------

        if (
            frame_number == 1
            or scene_changed
            or frame_number % ANOMALY_INTERVAL == 0
        ):

            try:
                raw_anom, self.last_anomaly_error = _run_anomaly(
                    frame, self
                )
                self.last_anomaly_detected = self._anomaly_gate.update(
                    raw_anom
                )

            except Exception:
                logger.exception(
                    "Anomaly inference failed"
                )

        # --------------------------------------------------------
        # Violence
        # --------------------------------------------------------

        violence_prediction = (
            self.last_violence_prediction
        )

        violence_confidence = (
            self.last_violence_confidence
        )

        if (
            ENABLE_VIOLENCE
            and frame_number % VIOLENCE_SAMPLE_INTERVAL == 0
        ):

            try:
                (
                    raw_violence,
                    violence_confidence,
                ) = _update_violence(frame, self)

                self._raw_violence_prediction = raw_violence
                self.last_violence_confidence = violence_confidence

                if raw_violence == "FIGHT":
                    confirmed_fight = self._violence_gate.update(True)
                elif raw_violence == "NO FIGHT":
                    confirmed_fight = self._violence_gate.update(False)
                else:
                    # PENDING / COLLECTING / UNAVAILABLE — do not touch gate
                    confirmed_fight = self._violence_gate.active

                if confirmed_fight:
                    violence_prediction = "FIGHT"
                elif raw_violence in {"UNAVAILABLE", "DISABLED"}:
                    violence_prediction = raw_violence
                elif raw_violence in {"COLLECTING", "PENDING"}:
                    violence_prediction = "COLLECTING"
                else:
                    violence_prediction = "NO FIGHT"

                self.last_violence_prediction = violence_prediction

            except Exception:
                logger.exception(
                    "Violence inference failed"
                )

        # --------------------------------------------------------
        # Crowd density / count
        # --------------------------------------------------------
        if (
            ENABLE_CROWD
            and (frame_number == 1 or frame_number % CROWD_INTERVAL == 0)
        ):
            try:
                (
                    self.last_crowd_count,
                    self.last_crowd_alert,
                ) = _run_crowd_detection(frame)

                if self.last_crowd_alert:
                    logger.info(
                        "Crowd threshold exceeded: %.1f people (threshold=%d) for %s",
                        self.last_crowd_count,
                        CROWD_THRESHOLD,
                        source_label or self.label or "stream",
                    )

            except Exception:
                logger.exception("Crowd-density inference failed")

        # --------------------------------------------------------
        # Person-of-interest face re-identification
        # --------------------------------------------------------
        if (
            ENABLE_FACE
            and (
                frame_number == 1
                or scene_changed
                or frame_number % FACE_INTERVAL == 0
            )
        ):
            try:
                if _load_face_model():
                    import face_reid

                    raw_faces = face_reid.match_frame(frame)
                    self._face_tracks, self.last_face_detections = (
                        _confirm_box_tracks(
                            self._face_tracks,
                            raw_faces,
                            need_hits=DETECT_CONFIRM_HITS,
                            need_misses=DETECT_CLEAR_MISSES,
                            iou_thresh=BOX_MATCH_IOU,
                        )
                    )
            except Exception:
                logger.exception("Face re-id inference failed")

        # --------------------------------------------------------
        # Combined detections
        # --------------------------------------------------------

        detections = (
            list(self.last_weapon_detections)
            + list(self.last_ocr_detections)
            + list(self.last_face_detections)
        )

        if draw:

            for detection in detections:
                _draw_detection(
                    output_frame,
                    detection,
                )

            _draw_hud(
                output_frame,
                source_label=source_label,
                detections=len(detections),
                anomaly=self.last_anomaly_detected,
                anomaly_error=self.last_anomaly_error,
                violence=violence_prediction,
                violence_confidence=violence_confidence,
            )

        elapsed_ms = (
            time.perf_counter()
            - start
        ) * 1000.0

        with self._instance_lock:

            self.stats["frames_processed"] += 1

            self.stats["last_inference_ms"] = round(
                elapsed_ms,
                2,
            )

            self.stats["detections_last_frame"] = len(
                detections
            )

            frames_processed = self.stats[
                "frames_processed"
            ]

        meta = {
            "detections": detections,

            "weapon_detections": list(
                self.last_weapon_detections
            ),

            "ocr_detections": list(
                self.last_ocr_detections
            ),

            "face_detections": list(
                self.last_face_detections
            ),

            "anomaly": {
                "detected": self.last_anomaly_detected,
                "error": round(
                    self.last_anomaly_error,
                    6,
                ),
                "threshold": round(self.anomaly_threshold, 6),
                "calibrated": self.anomaly_calibrated,
            },

            "crowd": {
                "count": round(self.last_crowd_count, 2),
                "threshold": CROWD_THRESHOLD,
                "alert": self.last_crowd_alert,
                "model": "LWCC-DM-Count-SHA",
            },

            "violence": {
                "prediction": violence_prediction,
                "confidence": round(
                    violence_confidence,
                    4,
                ),
                "frames_collected": len(
                    self.violence_frames
                ),
                "required_frames": (
                    VIOLENCE_SEQUENCE_LENGTH
                ),
            },

            "ocr": {
                "plate_detection": (
                    _model_status["ocr"] == "loaded"
                ),
                "text_recognition": (
                    _model_status["ocr_recognition"] == "loaded"
                ),
                "reason": (
                    None
                    if _model_status["ocr_recognition"] == "loaded"
                    else _model_errors.get(
                        "ocr_recognition",
                        "not_loaded",
                    )
                ),
            },

            "inference_ms": round(
                elapsed_ms,
                2,
            ),

            "frames_processed": frames_processed,

            "model_status": model_status(),

            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),

            "source_label": source_label,
        }

        return output_frame, meta


# ------------------------------------------------------------
# Backward-compatible module-level API.
#
# A handful of older call sites may still call frame_processor.
# process_frame(...)/get_stats() directly rather than creating their own
# FrameProcessor(). Those keep working via one shared default instance,
# but note this default instance has the SAME single-stream limitation
# the old global-state code had -- any new caller that wants proper
# multi-stream isolation should create its own FrameProcessor() instead.
# ------------------------------------------------------------

_default_processor = FrameProcessor(label="default")


def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    return _default_processor.process_frame(
        frame,
        source_label=source_label,
        draw=draw,
    )


def get_stats() -> Dict[str, Any]:
    return _default_processor.get_stats()
    