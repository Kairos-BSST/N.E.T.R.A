from __future__ import annotations
import logging
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import analysis_pipeline
import database
import drive_auth
from auth import current_user
from config import Config
from deps import state
from drive_client import DriveClient
from file_utils import safe_filename

logger = logging.getLogger("netra.drive")
router = APIRouter(tags=["drive"])


def _get_session_id(request: Request) -> Optional[str]:
    return request.cookies.get(Config.SESSION_COOKIE_NAME)


@router.get("/auth/google/login")
def google_login(user=Depends(current_user)):
    try:
        auth_url, oauth_state = drive_auth.get_authorization_url()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not start OAuth flow: {exc}") from exc
    response = RedirectResponse(url=auth_url)
    response.set_cookie("oauth_state", oauth_state, max_age=600, httponly=True, samesite="lax")
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    response: Response,
    code: str = None,
    state: str = None,
    error: str = None,
    user=Depends(current_user),
):
    if error:
        return RedirectResponse(url=f"/?drive_auth=denied&reason={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state in OAuth callback.")
    try:
        credentials = drive_auth.exchange_code_for_credentials(code, state)
        session_id = _get_session_id(request)
        drive_auth.store_credentials(session_id, credentials)
    except Exception as exc:
        logger.exception("OAuth code exchange failed")
        return RedirectResponse(url=f"/?drive_auth=error&reason={str(exc)[:100]}")
    redirect = RedirectResponse(url="/?drive_auth=success")
    redirect.delete_cookie("oauth_state")
    database.record_audit(user["id"], "DRIVE_AUTHORIZED")
    return redirect


@router.get("/auth/google/status")
def google_auth_status(request: Request, user=Depends(current_user)):
    return {"connected": drive_auth.is_connected(_get_session_id(request))}


@router.get("/drive/files")
def list_drive_files(request: Request, user=Depends(current_user)):
    credentials = drive_auth.get_credentials(_get_session_id(request))
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not connected to Google Drive. Call /auth/google/login first.")
    try:
        files = DriveClient(credentials).list_video_files()
    except Exception as exc:
        logger.exception("Drive files.list failed")
        raise HTTPException(status_code=502, detail=f"Drive API error: {exc}") from exc
    return {"files": files}


class DriveFetchRequest(BaseModel):
    file_id: str
    file_name: str


@router.post("/drive/fetch")
def fetch_drive_file(req: DriveFetchRequest, request: Request, user=Depends(current_user)):
    credentials = drive_auth.get_credentials(_get_session_id(request))
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not connected to Google Drive. Call /auth/google/login first.")

    name = safe_filename(req.file_name)
    Config.ensure_storage_dirs()
    local_path = os.path.join(Config.LOCAL_DRIVE_DOWNLOAD_DIR, name)
    try:
        size = DriveClient(credentials).download_file(req.file_id, local_path)
    except Exception as exc:
        logger.exception("Drive file download failed")
        raise HTTPException(status_code=502, detail=f"Drive download failed: {exc}") from exc

    state.mark_fetched(req.file_id, local_path, size)
    analysis = analysis_pipeline.queue_for_analysis(
        source=analysis_pipeline.SOURCE_DRIVE,
        local_path=local_path,
        original_name=name,
        extra={"file_id": req.file_id, "user_id": user["id"]},
    )
    database.record_audit(
        user["id"], "DRIVE_FETCHED", job_id=analysis["job_id"],
        resource_type="video", resource_id=analysis["job_id"],
        details={"file_name": name, "file_id": req.file_id, "source": "drive"},
    )
    try:
        analysis = analysis_pipeline.start_file_analysis(analysis["job_id"])
    except Exception as exc:
        logger.exception("Could not start analysis for Drive video")
        analysis_pipeline.update_job(
            analysis["job_id"], status="failed",
            message=f"Drive download succeeded but analysis could not start: {exc}", error=str(exc),
        )
        analysis = analysis_pipeline.get_job(analysis["job_id"]) or analysis
    return {"status": "fetched", "file_id": req.file_id, "local_path": local_path, "size_bytes": size, "analysis": analysis}