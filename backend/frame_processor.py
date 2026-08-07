"""
frame_processor.py
------------------
Shared AI inference pipeline for N.E.T.R.A.

Models:
1. Weapon detection       - YOLO (weapon.pt)
2. Number plate detection - YOLO (OCR.pt)
3. Anomaly detection      - Convolutional Autoencoder (anomaly.pth)
4. Violence detection     - MC3-18 (violence.pth)

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
import threading
import time
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

WEAPON_CONFIDENCE = 0.45
OCR_CONFIDENCE = 0.50
ANOMALY_THRESHOLD = 0.01

# CPU-friendly inference intervals
WEAPON_INTERVAL = 3
OCR_INTERVAL = 15
ANOMALY_INTERVAL = 5

VIOLENCE_SEQUENCE_LENGTH = 16
VIOLENCE_SAMPLE_INTERVAL = 2
VIOLENCE_RETAIN_FRAMES = 8


# ============================================================
# Global state
# ============================================================

_lock = threading.RLock()

_weapon_model = None
_ocr_detection_model = None
_ocr_recognition_model = None
_anomaly_model = None
_violence_model = None
_violence_transform = None

_torch = None

_model_status: Dict[str, str] = {
    "weapon": "not_loaded",
    "ocr": "not_loaded",
    "ocr_recognition": "not_loaded",
    "anomaly": "not_loaded",
    "violence": "not_loaded",
}

_model_errors: Dict[str, str] = {}

_frame_counter = 0

_violence_frames = deque(
    maxlen=VIOLENCE_SEQUENCE_LENGTH
)

_last_weapon_detections: List[Dict[str, Any]] = []
_last_ocr_detections: List[Dict[str, Any]] = []

_last_anomaly_detected = False
_last_anomaly_error = 0.0

_last_violence_prediction = "COLLECTING"
_last_violence_confidence = 0.0

_stats: Dict[str, Any] = {
    "frames_processed": 0,
    "last_inference_ms": 0.0,
    "detections_last_frame": 0,
}


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
    return os.path.join(
        _repo_root(),
        "models",
        filename,
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

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

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

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

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

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        model = (
            torchvision.models.video.mc3_18(
                weights=None
            )
        )

        model.fc = torch.nn.Linear(
            model.fc.in_features,
            2,
        )

        state_dict = torch.load(
            path,
            map_location=DEVICE,
        )

        model.load_state_dict(state_dict)

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


def get_stats() -> Dict[str, Any]:
    with _lock:
        return dict(_stats)


# ============================================================
# Weapon inference
# ============================================================

def _run_weapon_detection(
    frame: np.ndarray,
) -> List[Dict[str, Any]]:

    if not _load_weapon_model():
        return []

    detections: List[Dict[str, Any]] = []

    results = _weapon_model.predict(
        frame,
        conf=WEAPON_CONFIDENCE,
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

        if text:
            return str(text).strip(), float(score or 0.0)

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

    if not _load_ocr_model():
        return []

    detections: List[Dict[str, Any]] = []

    results = _ocr_detection_model.predict(
        frame,
        conf=OCR_CONFIDENCE,
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

    return detections


# ============================================================
# Anomaly inference
# ============================================================

def _run_anomaly(
    frame: np.ndarray,
) -> Tuple[bool, float]:

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

    return (
        error > ANOMALY_THRESHOLD,
        float(error),
    )


# ============================================================
# Violence inference
# ============================================================

def _update_violence(
    frame: np.ndarray,
) -> Tuple[str, float]:

    global _last_violence_prediction
    global _last_violence_confidence

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

    _violence_frames.append(
        processed
    )

    if (
        len(_violence_frames)
        < VIOLENCE_SEQUENCE_LENGTH
    ):
        return (
            _last_violence_prediction,
            _last_violence_confidence,
        )

    clip = np.array(
        _violence_frames
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

    tensor = torch.tensor(
        clip,
        dtype=torch.float32,
        device=DEVICE,
    )

    with torch.no_grad():

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

    _last_violence_confidence = float(
        confidence.item()
    )

    # Training labels:
    # 0 = fight
    # 1 = noFight

    if prediction_id == 0:
        _last_violence_prediction = "FIGHT"
    else:
        _last_violence_prediction = "NO FIGHT"

    retained = list(
        _violence_frames
    )[-VIOLENCE_RETAIN_FRAMES:]

    _violence_frames.clear()

    _violence_frames.extend(
        retained
    )

    return (
        _last_violence_prediction,
        _last_violence_confidence,
    )


# ============================================================
# Drawing helpers
# ============================================================

def _draw_detection(
    frame: np.ndarray,
    detection: Dict[str, Any],
) -> None:

    bbox = detection.get("bbox")

    if not bbox:
        return

    x1, y1, x2, y2 = bbox

    detection_type = detection.get(
        "type",
        "",
    )

    if detection_type == "weapon":
        color = (0, 0, 220)
    else:
        color = (0, 200, 0)

    cv2.rectangle(
        frame,
        (x1, y1),
        (x2, y2),
        color,
        2,
    )

    label = detection.get(
        "label",
        "",
    )

    confidence = detection.get(
        "confidence",
        0.0,
    )

    if (
        detection_type == "number_plate"
        and detection.get("plate_number")
    ):
        text = detection["plate_number"]
    else:
        text = (
            f"{label} "
            f"{confidence:.2f}"
        )

    cv2.putText(
        frame,
        text,
        (
            x1,
            max(18, y1 - 8),
        ),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )


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

def process_frame(
    frame: np.ndarray,
    *,
    source_label: str = "",
    draw: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:

    global _frame_counter
    global _last_weapon_detections
    global _last_ocr_detections
    global _last_anomaly_detected
    global _last_anomaly_error

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

    with _lock:
        _frame_counter += 1
        frame_number = _frame_counter

    # --------------------------------------------------------
    # Weapon
    # --------------------------------------------------------

    if (
        frame_number == 1
        or frame_number % WEAPON_INTERVAL == 0
    ):

        try:
            _last_weapon_detections = (
                _run_weapon_detection(frame)
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
        or frame_number % OCR_INTERVAL == 0
    ):

        try:
            _last_ocr_detections = (
                _run_ocr(frame)
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
        or frame_number % ANOMALY_INTERVAL == 0
    ):

        try:
            (
                _last_anomaly_detected,
                _last_anomaly_error,
            ) = _run_anomaly(frame)

        except Exception:
            logger.exception(
                "Anomaly inference failed"
            )

    # --------------------------------------------------------
    # Violence
    # --------------------------------------------------------

    violence_prediction = (
        _last_violence_prediction
    )

    violence_confidence = (
        _last_violence_confidence
    )

    if (
        frame_number
        % VIOLENCE_SAMPLE_INTERVAL
        == 0
    ):

        try:
            (
                violence_prediction,
                violence_confidence,
            ) = _update_violence(frame)

        except Exception:
            logger.exception(
                "Violence inference failed"
            )

    # --------------------------------------------------------
    # Combined detections
    # --------------------------------------------------------

    detections = (
        list(_last_weapon_detections)
        + list(_last_ocr_detections)
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
            anomaly=_last_anomaly_detected,
            anomaly_error=_last_anomaly_error,
            violence=violence_prediction,
            violence_confidence=violence_confidence,
        )

    elapsed_ms = (
        time.perf_counter()
        - start
    ) * 1000.0

    with _lock:

        _stats["frames_processed"] += 1

        _stats["last_inference_ms"] = round(
            elapsed_ms,
            2,
        )

        _stats["detections_last_frame"] = len(
            detections
        )

        frames_processed = _stats[
            "frames_processed"
        ]

    meta = {
        "detections": detections,

        "weapon_detections": list(
            _last_weapon_detections
        ),

        "ocr_detections": list(
            _last_ocr_detections
        ),

        "anomaly": {
            "detected": _last_anomaly_detected,
            "error": round(
                _last_anomaly_error,
                6,
            ),
            "threshold": ANOMALY_THRESHOLD,
        },

        "violence": {
            "prediction": violence_prediction,
            "confidence": round(
                violence_confidence,
                4,
            ),
            "frames_collected": len(
                _violence_frames
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