"""
inputs/drive.py
---------------
Google Drive OAuth + video fetch.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import analysis_pipeline
import drive_auth
from config import Config
from deps import state
from drive_client import DriveClient
from file_utils import safe_filename

logger = logging.getLogger("netra.drive")
router = APIRouter(tags=["drive"])


def _get_session_id(request: Request) -> Optional[str]:
    return request.cookies.get(Config.SESSION_COOKIE_NAME)


@router.get("/auth/google/login")
def google_login():
    try:
        auth_url, oauth_state = drive_auth.get_authorization_url()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not start OAuth flow: {e}")

    response = RedirectResponse(url=auth_url)
    response.set_cookie("oauth_state", oauth_state, max_age=600, httponly=True)
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, response: Response, code: str = None,
                     state: str = None, error: str = None):
    if error:
        return RedirectResponse(url=f"/?drive_auth=denied&reason={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state in OAuth callback.")

    try:
        credentials = drive_auth.exchange_code_for_credentials(code, state)
    except Exception as e:
        logger.exception("OAuth code exchange failed")
        return RedirectResponse(url=f"/?drive_auth=error&reason={str(e)[:100]}")

    session_id = _get_session_id(request) or drive_auth.new_session_id()
    drive_auth.store_credentials(session_id, credentials)

    redirect = RedirectResponse(url="/?drive_auth=success")
    redirect.set_cookie(
        Config.SESSION_COOKIE_NAME, session_id,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        samesite="lax",
    )
    redirect.delete_cookie("oauth_state")
    return redirect


@router.get("/auth/google/status")
def google_auth_status(request: Request):
    session_id = _get_session_id(request)
    return {"connected": drive_auth.is_connected(session_id)}


@router.get("/drive/files")
def list_drive_files(request: Request):
    session_id = _get_session_id(request)
    credentials = drive_auth.get_credentials(session_id)
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not connected to Google Drive. Call /auth/google/login first.")

    try:
        drive = DriveClient(credentials)
        files = drive.list_video_files()
    except Exception as e:
        logger.exception("Drive files.list failed")
        raise HTTPException(status_code=502, detail=f"Drive API error: {e}")

    return {"files": files}


class DriveFetchRequest(BaseModel):
    file_id: str
    file_name: str


@router.post("/drive/fetch")
def fetch_drive_file(req: DriveFetchRequest, request: Request):
    session_id = _get_session_id(request)
    credentials = drive_auth.get_credentials(session_id)
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not connected to Google Drive. Call /auth/google/login first.")

    name = safe_filename(req.file_name)
    local_path = os.path.join(Config.LOCAL_DRIVE_DOWNLOAD_DIR, name)

    try:
        drive = DriveClient(credentials)
        size = drive.download_file(req.file_id, local_path)
    except Exception as e:
        logger.exception("Drive file download failed")
        raise HTTPException(status_code=502, detail=f"Drive download failed: {e}")

    state.mark_fetched(req.file_id, local_path, size)

    analysis = analysis_pipeline.queue_for_analysis(
        source=analysis_pipeline.SOURCE_DRIVE,
        local_path=local_path,
        original_name=name,
        extra={"file_id": req.file_id},
    )

    return {
        "status": "fetched",
        "file_id": req.file_id,
        "local_path": local_path,
        "size_bytes": size,
        "analysis": analysis,
    }
