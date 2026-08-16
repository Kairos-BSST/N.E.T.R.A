"""Person-of-interest gallery: enroll faces (max 2 images) and serve stored photos."""
from __future__ import annotations

import os
import uuid
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import database
import face_reid
from auth import current_user
from config import Config

router = APIRouter(tags=["poi"])

ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp",
}
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_IMAGES_PER_UPLOAD = 2
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _reload_gallery() -> None:
    try:
        face_reid.reload_gallery(database.list_poi_embeddings())
    except Exception:
        pass


def _decode_upload(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise HTTPException(status_code=400, detail="Could not decode image. Use JPG/PNG.")
    return image


def _safe_ext(filename: str, content_type: Optional[str]) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in ALLOWED_EXTS:
        return ".jpg" if ext == ".jpeg" else ext
    if content_type in {"image/png"}:
        return ".png"
    if content_type in {"image/webp"}:
        return ".webp"
    return ".jpg"


def _upload_list(images) -> List[UploadFile]:
    if images is None:
        return []
    if isinstance(images, UploadFile):
        items = [images]
    else:
        items = list(images)
    return [f for f in items if f is not None and getattr(f, "filename", None)]


@router.get("/poi")
def list_poi(user=Depends(current_user)):
    _ = user
    return {
        "pois": database.list_pois(),
        "face_models": face_reid.status(),
        "max_images_per_person": MAX_IMAGES_PER_UPLOAD,
    }


@router.get("/poi/{poi_id}")
def get_poi(poi_id: str, user=Depends(current_user)):
    _ = user
    item = database.get_poi(poi_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Person-of-interest not found.")
    return item


@router.post("/poi")
async def create_poi(
    name: str = Form(...),
    notes: str = Form(""),
    images: List[UploadFile] = File(...),
    user=Depends(current_user),
):
    clean_name = (name or "").strip()
    if len(clean_name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters.")

    files = _upload_list(images)
    if not files:
        raise HTTPException(status_code=400, detail="Upload 1 or 2 face images.")
    if len(files) > MAX_IMAGES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Upload at most {MAX_IMAGES_PER_UPLOAD} images at a time.")

    if not face_reid.ensure_models():
        raise HTTPException(
            status_code=503,
            detail=f"Face models unavailable: {face_reid.status().get('error') or 'load failed'}",
        )

    poi_id = uuid.uuid4().hex[:12]
    Config.ensure_storage_dirs()
    folder = os.path.join(os.path.abspath(Config.POI_GALLERY_DIR), poi_id)
    os.makedirs(folder, exist_ok=True)

    prepared = []
    for upload in files:
        content_type = (upload.content_type or "").lower()
        if content_type and content_type not in ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty image upload.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Each image must be under 8 MB.")
        image = _decode_upload(raw)
        emb, meta = face_reid.embed_face_in_image(image)
        if emb is None:
            raise HTTPException(
                status_code=400,
                detail=f"No face detected in '{upload.filename}'. Use a clear frontal face photo.",
            )
        prepared.append(
            {
                "raw": raw,
                "filename": upload.filename or "face.jpg",
                "content_type": content_type,
                "embedding": face_reid.embedding_to_bytes(emb),
                "meta": meta or {},
                "image": image,
            }
        )

    database.create_poi(
        poi_id=poi_id,
        name=clean_name,
        notes=(notes or "").strip(),
        created_by=user.get("id"),
        enabled=True,
    )

    faces_out = []
    try:
        for item in prepared:
            face_id = uuid.uuid4().hex[:12]
            ext = _safe_ext(item["filename"], item["content_type"])
            file_name = f"{face_id}{ext}"
            path = os.path.join(folder, file_name)
            ok = cv2.imwrite(path, item["image"])
            if not ok:
                with open(path, "wb") as fh:
                    fh.write(item["raw"])
            face = database.add_poi_face(
                face_id=face_id,
                poi_id=poi_id,
                file_path=path,
                file_name=file_name,
                embedding=item["embedding"],
                detect_score=(item["meta"] or {}).get("score"),
                bbox=(item["meta"] or {}).get("bbox"),
            )
            faces_out.append(face)
    except Exception:
        database.delete_poi(poi_id)
        raise

    _reload_gallery()
    database.record_audit(
        user["id"], "POI_ENROLLED", resource_type="poi", resource_id=poi_id,
        details={"name": clean_name, "face_count": len(faces_out)},
    )
    return {"poi": database.get_poi(poi_id), "faces": faces_out}


@router.delete("/poi/{poi_id}")
def remove_poi(poi_id: str, user=Depends(current_user)):
    if not database.delete_poi(poi_id):
        raise HTTPException(status_code=404, detail="Person-of-interest not found.")
    _reload_gallery()
    database.record_audit(user["id"], "POI_DELETED", resource_type="poi", resource_id=poi_id)
    return {"ok": True, "poi_id": poi_id}


@router.get("/poi/{poi_id}/faces/{face_id}/image")
def get_face_image(poi_id: str, face_id: str, user=Depends(current_user)):
    _ = user
    face = database.get_poi_face(poi_id, face_id)
    if face is None:
        raise HTTPException(status_code=404, detail="Face image not found.")
    path = face.get("file_path") or ""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Stored image file missing.")
    return FileResponse(path, media_type="image/jpeg", filename=face.get("file_name") or "face.jpg")
