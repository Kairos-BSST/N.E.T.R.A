"""
face_reid.py
------------
Person-of-interest face detection + re-identification.

Uses OpenCV Zoo models (YuNet detector + SFace recognizer). Models are
downloaded once into models/face/ on first use.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("netra.face_reid")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
FACE_MODEL_DIR = os.getenv(
    "FACE_MODEL_DIR",
    os.path.join(PROJECT_ROOT, "models", "face"),
)

YUNET_NAME = "face_detection_yunet_2023mar.onnx"
SFACE_NAME = "face_recognition_sface_2021dec.onnx"
YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    f"face_detection_yunet/{YUNET_NAME}"
)
SFACE_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    f"face_recognition_sface/{SFACE_NAME}"
)

FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.363"))
FACE_DETECT_SCORE = float(os.getenv("FACE_DETECT_SCORE", "0.70"))
FACE_DETECT_NMS = float(os.getenv("FACE_DETECT_NMS", "0.30"))
FACE_TOP_K = int(os.getenv("FACE_TOP_K", "8"))

_lock = threading.RLock()
_detector = None
_recognizer = None
_gallery: List[Dict[str, Any]] = []
_gallery_loaded = False
_status = "not_loaded"
_error: Optional[str] = None


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    logger.info("Downloading face model -> %s", dest)
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, dest)


def _ensure_model(path: str, url: str) -> str:
    if os.path.isfile(path) and os.path.getsize(path) > 10_000:
        return path
    _download(url, path)
    if not os.path.isfile(path) or os.path.getsize(path) < 10_000:
        raise RuntimeError(f"Failed to download face model: {path}")
    return path


def ensure_models() -> bool:
    global _detector, _recognizer, _status, _error
    with _lock:
        if _detector is not None and _recognizer is not None:
            return True
        if _status == "failed":
            return False
        try:
            yunet = _ensure_model(os.path.join(FACE_MODEL_DIR, YUNET_NAME), YUNET_URL)
            sface = _ensure_model(os.path.join(FACE_MODEL_DIR, SFACE_NAME), SFACE_URL)
            _detector = cv2.FaceDetectorYN.create(
                yunet, "", (320, 320), FACE_DETECT_SCORE, FACE_DETECT_NMS, FACE_TOP_K
            )
            _recognizer = cv2.FaceRecognizerSF.create(sface, "")
            _status = "loaded"
            _error = None
            logger.info("Face re-id models loaded (YuNet + SFace).")
            return True
        except Exception as exc:
            _status = "failed"
            _error = str(exc)
            _detector = None
            _recognizer = None
            logger.exception("Unable to load face re-id models")
            return False


def status() -> Dict[str, Any]:
    return {"status": _status, "error": _error, "gallery_size": len(_gallery)}


def _align_crop(frame: np.ndarray, face_row: np.ndarray) -> np.ndarray:
    assert _recognizer is not None
    return _recognizer.alignCrop(frame, face_row)


def _embed(aligned: np.ndarray) -> np.ndarray:
    assert _recognizer is not None
    feat = _recognizer.feature(aligned)
    return np.asarray(feat, dtype=np.float32).reshape(-1)


def detect_faces(frame: np.ndarray) -> List[Dict[str, Any]]:
    if frame is None or frame.size == 0:
        return []
    if not ensure_models():
        return []

    h, w = frame.shape[:2]
    with _lock:
        assert _detector is not None
        _detector.setInputSize((w, h))
        _retval, faces = _detector.detect(frame)

    if faces is None or len(faces) == 0:
        return []

    out: List[Dict[str, Any]] = []
    for row in faces:
        x, y, bw, bh = row[0:4]
        score = float(row[14]) if len(row) > 14 else float(row[-1])
        x1 = max(0, int(x))
        y1 = max(0, int(y))
        x2 = min(w - 1, int(x + bw))
        y2 = min(h - 1, int(y + bh))
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({"bbox": [x1, y1, x2, y2], "score": score, "face_row": row})
    return out


def embed_face_in_image(image_bgr: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
    faces = detect_faces(image_bgr)
    if not faces:
        return None, None
    faces.sort(
        key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]),
        reverse=True,
    )
    top = faces[0]
    with _lock:
        aligned = _align_crop(image_bgr, top["face_row"])
        vec = _embed(aligned)
    return vec, {"bbox": top["bbox"], "score": top["score"]}


def embedding_to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def embedding_from_bytes(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)


def reload_gallery(entries: List[Dict[str, Any]]) -> int:
    global _gallery, _gallery_loaded
    loaded: List[Dict[str, Any]] = []
    for item in entries:
        emb = item.get("embedding")
        if emb is None:
            continue
        if isinstance(emb, (bytes, bytearray, memoryview)):
            vec = embedding_from_bytes(bytes(emb))
        else:
            vec = np.asarray(emb, dtype=np.float32)
        if vec.size == 0:
            continue
        loaded.append(
            {
                "poi_id": item["poi_id"],
                "name": item.get("name") or "POI",
                "face_id": item.get("face_id"),
                "embedding": vec,
            }
        )
    with _lock:
        _gallery = loaded
        _gallery_loaded = True
    logger.info("Face gallery reloaded: %d embeddings", len(loaded))
    return len(loaded)


def ensure_gallery_loaded() -> None:
    global _gallery_loaded
    if _gallery_loaded:
        return
    try:
        import database

        reload_gallery(database.list_poi_embeddings())
    except Exception:
        logger.exception("Could not load POI face gallery from database")
        with _lock:
            _gallery_loaded = True


def match_embedding(vec: np.ndarray, threshold: Optional[float] = None) -> Optional[Dict[str, Any]]:
    ensure_gallery_loaded()
    thr = FACE_MATCH_THRESHOLD if threshold is None else float(threshold)
    best = None
    best_score = -1.0
    with _lock:
        gallery = list(_gallery)
    for item in gallery:
        score = cosine_similarity(vec, item["embedding"])
        if score > best_score:
            best_score = score
            best = item
    if best is None or best_score < thr:
        return None
    return {
        "poi_id": best["poi_id"],
        "name": best["name"],
        "face_id": best.get("face_id"),
        "similarity": round(best_score, 4),
        "threshold": thr,
    }


def match_frame(frame: np.ndarray, *, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
    if not ensure_models():
        return []
    ensure_gallery_loaded()
    with _lock:
        if not _gallery:
            return []

    faces = detect_faces(frame)
    matches: List[Dict[str, Any]] = []
    for face in faces:
        try:
            with _lock:
                aligned = _align_crop(frame, face["face_row"])
                vec = _embed(aligned)
            hit = match_embedding(vec, threshold=threshold)
            if not hit:
                continue
            matches.append(
                {
                    "type": "face",
                    "label": hit["name"],
                    "confidence": hit["similarity"],
                    "bbox": face["bbox"],
                    "detect_score": round(float(face["score"]), 4),
                    "poi_id": hit["poi_id"],
                    "face_id": hit.get("face_id"),
                    "similarity": hit["similarity"],
                    "match_threshold": hit["threshold"],
                }
            )
        except Exception:
            logger.exception("Face match failed for one detection")
    return matches
