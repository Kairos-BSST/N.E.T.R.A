"""
inputs/upload.py
----------------
Local device video upload.
"""

import logging
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

import analysis_pipeline
from config import Config
from deps import state
from file_utils import is_allowed_video, safe_filename

logger = logging.getLogger("netra.upload")
router = APIRouter(tags=["upload"])


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """
    Accept a video file from the Signal Intake upload panel.
    Saves under LOCAL_UPLOAD_DIR and queues the analysis placeholder.
    """
    original = file.filename or "video"
    name = safe_filename(original)
    content_type = file.content_type

    if not is_allowed_video(name, content_type):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{content_type or 'unknown'}' for '{original}'. "
                "Upload a video file (mp4, mov, avi, mkv, webm, …)."
            ),
        )

    stem, ext = os.path.splitext(name)
    unique_name = f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
    local_path = os.path.join(Config.LOCAL_UPLOAD_DIR, unique_name)

    Config.ensure_storage_dirs()
    size = 0
    chunk_size = 1024 * 1024

    try:
        with open(local_path, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > Config.MAX_UPLOAD_BYTES:
                    out.close()
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max upload size of {Config.MAX_UPLOAD_BYTES} bytes.",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload write failed")
        try:
            if os.path.isfile(local_path):
                os.remove(local_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    finally:
        await file.close()

    if size == 0:
        try:
            os.remove(local_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    state.mark_fetched(f"upload:{unique_name}", local_path, size)

    analysis = analysis_pipeline.queue_for_analysis(
        source=analysis_pipeline.SOURCE_UPLOAD,
        local_path=local_path,
        original_name=original,
        extra={"content_type": content_type, "stored_as": unique_name},
    )

    return {
        "status": "uploaded",
        "original_name": original,
        "stored_as": unique_name,
        "local_path": local_path,
        "size_bytes": size,
        "content_type": content_type,
        "analysis": analysis,
    }
