"""
api.py
------
Backend for the Signal Intake console. Serves the real frontend and
handles the Google Drive OAuth + fetch flow.

Run it:
    python -m uvicorn api:app --reload --port 8000

Then open:
    http://127.0.0.1:8000/

Endpoints:
    GET  /                              -> serves the real frontend
    GET  /auth/google/login             -> redirects to Google's consent screen
    GET  /auth/google/callback          -> handles the OAuth redirect back
    GET  /auth/google/status            -> is this browser session connected?
    GET  /drive/files                   -> list the user's Drive video files
    POST /drive/fetch                   -> download a chosen file locally
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

import drive_auth
from drive_client import DriveClient
from state_tracker import StateTracker
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("netra.api")

FRONTEND_HTML_PATH = os.path.join(os.path.dirname(__file__), "frontend", "index.html")

app = FastAPI(
    title="N.E.T.R.A Signal Intake API",
    description="Backend for the Signal Intake console - real Google Drive OAuth fetch.",
    version="1.0.0",
)

state = StateTracker(Config.STATE_FILE_PATH)


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the actual signal-intake.html frontend from the same origin as the API."""
    if not os.path.isfile(FRONTEND_HTML_PATH):
        raise HTTPException(status_code=404, detail="Frontend file not found at frontend/index.html")
    with open(FRONTEND_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _get_session_id(request: Request) -> Optional[str]:
    return request.cookies.get(Config.SESSION_COOKIE_NAME)


@app.get("/auth/google/login")
def google_login():
    """
    Step 1 of OAuth. Redirects the browser to Google's consent screen.
    The frontend's "CONNECT GOOGLE DRIVE" button navigates here directly
    (a full page redirect, not a fetch() call).
    """
    try:
        auth_url, oauth_state = drive_auth.get_authorization_url()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not start OAuth flow: {e}")

    response = RedirectResponse(url=auth_url)
    response.set_cookie("oauth_state", oauth_state, max_age=600, httponly=True)
    return response


@app.get("/auth/google/callback")
def google_callback(request: Request, response: Response, code: str = None,
                     state: str = None, error: str = None):
    """
    Step 2 of OAuth. Google redirects here after the user approves (or
    denies) access. We exchange the code for credentials, store them
    against a session cookie, and send the browser back to the console.
    """
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


@app.get("/auth/google/status")
def google_auth_status(request: Request):
    """Lets the frontend check on page load whether this browser session is already connected."""
    session_id = _get_session_id(request)
    return {"connected": drive_auth.is_connected(session_id)}


@app.get("/drive/files")
def list_drive_files(request: Request):
    """Real files.list call - returns the user's video files from Drive."""
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


@app.post("/drive/fetch")
def fetch_drive_file(req: DriveFetchRequest, request: Request):
    """Real files.get / media download call - pulls the chosen file's bytes down to local disk."""
    session_id = _get_session_id(request)
    credentials = drive_auth.get_credentials(session_id)
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not connected to Google Drive. Call /auth/google/login first.")

    local_path = os.path.join(Config.LOCAL_DRIVE_DOWNLOAD_DIR, req.file_name)

    try:
        drive = DriveClient(credentials)
        size = drive.download_file(req.file_id, local_path)
    except Exception as e:
        logger.exception("Drive file download failed")
        raise HTTPException(status_code=502, detail=f"Drive download failed: {e}")

    state.mark_fetched(req.file_id, local_path, size)

    return {
        "status": "fetched",
        "file_id": req.file_id,
        "local_path": local_path,
        "size_bytes": size,
    }


@app.get("/videos")
def list_fetched_videos():
    """Returns every video fetched so far, according to the persisted state file."""
    return state.all_fetched()