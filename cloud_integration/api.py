"""
api.py
------
A small REST API around the cloud video-fetch module, purely so you can
test everything via HTTP (curl, Postman, or the free interactive docs at
/docs) without needing any frontend at all.

Run it:
    uvicorn api:app --reload --port 8000

Then open:
    http://127.0.0.1:8000/docs

That gives you a Swagger UI where you can click "Try it out" on each
endpoint and see real request/response data - effectively Postman built
into the browser.

Endpoints:
    GET  /health                       -> is the service up, is GCS reachable
    POST /fetch                        -> run one fetch cycle right now
    GET  /videos                       -> list every video fetched so far (from state)
    GET  /videos/signed-url?blob_name= -> get a temporary playback URL for a video
    POST /upload                       -> upload a local file up to the bucket
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

import drive_auth
from drive_client import DriveClient
from video_fetcher import VideoFetcher
from config import Config

FRONTEND_HTML_PATH = os.path.join(os.path.dirname(__file__), "frontend", "index.html")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("netra.api")

app = FastAPI(
    title="N.E.T.R.A Cloud Fetch API",
    description="Backend for the Signal Intake console: GCS live/upload pipeline + real Google Drive OAuth fetch.",
    version="1.0.0",
)


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the actual signal-intake.html frontend from the same origin as the API."""
    if not os.path.isfile(FRONTEND_HTML_PATH):
        raise HTTPException(status_code=404, detail="Frontend file not found at frontend/index.html")
    with open(FRONTEND_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()

# Track videos received via callback during this process's lifetime,
# purely for the /fetch endpoint's response - the real source of truth
# is always fetcher.state (fetch_state.json).
_last_fetch_new_videos = []


def _on_new_video(local_path: str, blob_name: str) -> None:
    logger.info("New video ready: %s (source: %s)", local_path, blob_name)
    _last_fetch_new_videos.append({"local_path": local_path, "blob_name": blob_name})


# Created lazily so the API can still start up (and /health can report the
# problem clearly) even if GCP credentials aren't configured yet.
_fetcher: Optional[VideoFetcher] = None
_init_error: Optional[str] = None

try:
    _fetcher = VideoFetcher(on_new_video=_on_new_video)
except Exception as e:
    _init_error = str(e)
    logger.error("VideoFetcher failed to initialize: %s", e)


class FetchResponse(BaseModel):
    new_videos_count: int
    new_videos: list


class SignedUrlResponse(BaseModel):
    blob_name: str
    signed_url: str
    expiry_minutes: int


@app.get("/health")
def health():
    """Check the API is up and whether the GCS connection is actually working."""
    if _fetcher is None:
        raise HTTPException(
            status_code=503,
            detail=f"GCS client not initialized: {_init_error}",
        )
    return {
        "status": "ok",
        "bucket": Config.GCS_BUCKET_NAME,
        "prefix": Config.GCS_VIDEO_PREFIX,
    }


@app.post("/fetch", response_model=FetchResponse)
def trigger_fetch():
    """Run a single fetch cycle right now and return what was newly downloaded."""
    if _fetcher is None:
        raise HTTPException(status_code=503, detail=f"GCS client not initialized: {_init_error}")

    _last_fetch_new_videos.clear()
    try:
        count = _fetcher.fetch_once()
    except Exception as e:
        logger.exception("Fetch cycle failed")
        raise HTTPException(status_code=500, detail=str(e))

    return FetchResponse(new_videos_count=count, new_videos=list(_last_fetch_new_videos))


@app.get("/videos")
def list_videos():
    """Return every video fetched so far, according to the persisted state file."""
    if _fetcher is None:
        raise HTTPException(status_code=503, detail=f"GCS client not initialized: {_init_error}")
    return _fetcher.state.all_fetched()


@app.get("/videos/signed-url", response_model=SignedUrlResponse)
def get_signed_url(
    blob_name: str = Query(..., description="Object path in the bucket, e.g. uploads/sample.mp4"),
    expiry_minutes: int = Query(60, ge=1, le=1440, description="Link validity in minutes"),
):
    """Get a temporary, secure URL for streaming a specific video directly from GCS."""
    if _fetcher is None:
        raise HTTPException(status_code=503, detail=f"GCS client not initialized: {_init_error}")
    try:
        url = _fetcher.gcs.generate_signed_url(blob_name, expiry_minutes=expiry_minutes)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not generate signed URL: {e}")

    return SignedUrlResponse(blob_name=blob_name, signed_url=url, expiry_minutes=expiry_minutes)


class UploadRequest(BaseModel):
    local_path: str
    destination_blob_name: str


@app.post("/upload")
def upload_file(req: UploadRequest):
    """
    Upload a local file (already on the server's disk) up to the bucket.
    Useful for testing the upload path, or for pushing processed clips /
    evidence back to the cloud once your analytics module produces them.
    """
    if _fetcher is None:
        raise HTTPException(status_code=503, detail=f"GCS client not initialized: {_init_error}")
    try:
        _fetcher.gcs.upload_file(req.local_path, req.destination_blob_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "uploaded", "destination": req.destination_blob_name}


# =====================================================================
# GOOGLE DRIVE OAUTH + FETCH
# This is the real implementation of the "Fetch from Drive" panel -
# replaces the mock authorization flow with actual OAuth + files.list/
# files.get calls against the Google Drive API.
# =====================================================================

def _get_session_id(request: Request) -> Optional[str]:
    return request.cookies.get(Config.SESSION_COOKIE_NAME)


@app.get("/auth/google/login")
def google_login():
    """
    Step 1 of the OAuth flow. Redirects the browser to Google's consent
    screen. The frontend's "CONNECT GOOGLE DRIVE" button should navigate
    here (a full page redirect, not a fetch() call, since the user needs
    to actually see and interact with Google's login page).
    """
    try:
        auth_url, state = drive_auth.get_authorization_url()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not start OAuth flow: {e}")

    response = RedirectResponse(url=auth_url)
    # Store the OAuth state in a short-lived cookie so the callback can
    # cross-check it (defense in depth, on top of the server-side check).
    response.set_cookie("oauth_state", state, max_age=600, httponly=True)
    return response


@app.get("/auth/google/callback")
def google_callback(request: Request, response: Response, code: str = None, state: str = None, error: str = None):
    """
    Step 2 of the OAuth flow. Google redirects here after the user
    approves (or denies) access. We exchange the code for credentials
    and store them against a session cookie, then send the browser back
    to the main page with a query flag the frontend JS checks for.
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
        max_age=60 * 60 * 24 * 7,  # 1 week
        httponly=True,
        samesite="lax",
    )
    redirect.delete_cookie("oauth_state")
    return redirect


@app.get("/auth/google/status")
def google_auth_status(request: Request):
    """Lets the frontend check on page load whether this browser already has a connected Drive session."""
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
    """
    Real files.get / media download call - pulls the chosen file's bytes
    down to local disk. If PUSH_DRIVE_FETCHES_TO_GCS is enabled, also
    uploads it into the same GCS bucket the Live/camera pipeline uses, so
    downstream analytics has one consistent source of truth regardless of
    where the video originated.
    """
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

    result = {
        "status": "fetched",
        "file_id": req.file_id,
        "local_path": local_path,
        "size_bytes": size,
        "pushed_to_gcs": False,
    }

    if Config.PUSH_DRIVE_FETCHES_TO_GCS and _fetcher is not None:
        gcs_blob_name = f"drive-imports/{req.file_name}"
        try:
            _fetcher.gcs.upload_file(local_path, gcs_blob_name)
            _fetcher.state.mark_fetched(gcs_blob_name, local_path, size)
            result["pushed_to_gcs"] = True
            result["gcs_blob_name"] = gcs_blob_name
        except Exception as e:
            logger.warning("Fetched from Drive but failed to push to GCS: %s", e)
            result["gcs_push_error"] = str(e)

    return result