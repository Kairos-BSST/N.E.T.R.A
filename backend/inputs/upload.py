import logging
import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from auth import current_user
import analysis_pipeline
import database
from config import Config
from deps import state
from file_utils import is_allowed_video, safe_filename

logger = logging.getLogger("netra.upload")

router = APIRouter(tags=["upload"])


@router.post("/upload")
async def upload_video(file: UploadFile = File(...), user=Depends(current_user)):
    original = file.filename or "video"
    name = safe_filename(original)
    content_type = file.content_type

    # Validate file type
    if not is_allowed_video(
        name,
        content_type,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type "
                f"'{content_type or 'unknown'}' "
                f"for '{original}'. "
                "Upload a supported video file."
            ),
        )

    # Generate safe unique filename
    stem, ext = os.path.splitext(name)
    unique_name = (
        f"{stem}_"
        f"{uuid.uuid4().hex[:8]}"
        f"{ext}"
    )

    local_path = os.path.join(
        Config.LOCAL_UPLOAD_DIR,
        unique_name,
    )

    Config.ensure_storage_dirs()

    size = 0

    chunk_size = 1024 * 1024

    # Save uploaded video
    try:
        with open(local_path, "wb") as out:
            while True:
                chunk = await file.read(
                    chunk_size
                )
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
                        detail=(
                            "File exceeds max upload "
                            f"size of "
                            f"{Config.MAX_UPLOAD_BYTES} "
                            "bytes."
                        ),
                    )
                out.write(chunk)

    except HTTPException:
        raise

    except Exception as exc:

        logger.exception(
            "Upload write failed"
        )

        try:
            if os.path.isfile(local_path):
                os.remove(local_path)

        except OSError:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {exc}",
        )

    finally:

        await file.close()

    # Reject empty uploads
    if size == 0:

        try:
            os.remove(local_path)
        except OSError:
            pass

        raise HTTPException(
            status_code=400,
            detail="Empty file uploaded.",
        )

    # Register fetched/local media
    state.mark_fetched(
        f"upload:{unique_name}",
        local_path,
        size,
    )

    # Create analysis job
    analysis = (
        analysis_pipeline.queue_for_analysis(
            source=analysis_pipeline.SOURCE_UPLOAD,
            local_path=local_path,
            original_name=original,
            extra={
                "user_id": user["id"],
                "content_type": content_type,
                "stored_as": unique_name,
            },
        )
    )

    database.record_audit(
        user["id"], "VIDEO_UPLOADED", job_id=analysis["job_id"],
        resource_type="video", resource_id=analysis["job_id"],
        details={"original_name": original, "source": "upload", "size_bytes": size},
    )

    # Start background analysis
    try:

        analysis = (
            analysis_pipeline.start_file_analysis(
                analysis["job_id"]
            )
        )

    except Exception as exc:

        logger.exception(
            "Could not start analysis for uploaded video"
        )

        analysis_pipeline.update_job(
            analysis["job_id"],
            status="failed",
            message=(
                "Upload succeeded but analysis "
                f"could not start: {exc}"
            ),
            error=str(exc),
        )

        updated = analysis_pipeline.get_job(
            analysis["job_id"]
        )

        if updated is not None:
            analysis = updated

    # Response
    return {
        "status": "uploaded",
        "original_name": original,
        "stored_as": unique_name,
        "local_path": local_path,
        "size_bytes": size,
        "content_type": content_type,
        "analysis": analysis,
    }