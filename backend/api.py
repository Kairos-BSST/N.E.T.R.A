"""
api.py
------
N.E.T.R.A Signal Intake API.

Serves the frontend/ folder and mounts one router per input type:
    inputs/live.py    — live / CCTV / RTSP
    inputs/upload.py  — local video upload
    inputs/drive.py   — Google Drive OAuth + fetch
    inputs/analysis.py — shared model-analysis placeholder

Run from the backend/ directory:
    python -m uvicorn api:app --reload --port 8000

Then open:
    http://127.0.0.1:8000/
"""

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import Config
from inputs import analysis as analysis_input
from inputs import drive as drive_input
from inputs import live as live_input
from inputs import upload as upload_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("netra.api")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend"))

app = FastAPI(
    title="N.E.T.R.A Signal Intake API",
    description="Backend for Signal Intake — live CCTV/webcam, upload, Drive, shared AI frame pipeline.",
    version="1.3.0",
)

Config.ensure_storage_dirs()

app.include_router(live_input.router)
app.include_router(upload_input.router)
app.include_router(drive_input.router)
app.include_router(analysis_input.router)

# Static assets from ../frontend (css/, js/)
if os.path.isdir(FRONTEND_DIR):
    css_dir = os.path.join(FRONTEND_DIR, "css")
    js_dir = os.path.join(FRONTEND_DIR, "js")
    if os.path.isdir(css_dir):
        app.mount("/css", StaticFiles(directory=css_dir), name="css")
    if os.path.isdir(js_dir):
        app.mount("/js", StaticFiles(directory=js_dir), name="js")


@app.get("/")
def serve_frontend():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail=f"Frontend not found at {index_path}")
    return FileResponse(index_path, media_type="text/html")
