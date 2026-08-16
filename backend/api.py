"""N.E.T.R.A Signal Intake API."""
from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import Config
import database
from inputs import analysis as analysis_input
from inputs import alerts as alerts_input
from inputs import drive as drive_input
from inputs import live as live_input
from inputs import poi as poi_input
from inputs import upload as upload_input
from auth import router as auth_router
import frame_processor
import face_reid
import database as db_mod

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("netra.api")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend"))

app = FastAPI(
    title="N.E.T.R.A Signal Intake API",
    description="Backend for Signal Intake — live CCTV/webcam, upload, Drive, shared AI frame pipeline and alerting.",
    version="1.5.0",
)

Config.ensure_storage_dirs()
database.init_db()

app.include_router(live_input.router)
app.include_router(upload_input.router)
app.include_router(drive_input.router)
app.include_router(analysis_input.router)
app.include_router(alerts_input.router)
app.include_router(poi_input.router)
app.include_router(auth_router)

if os.path.isdir(FRONTEND_DIR):
    css_dir = os.path.join(FRONTEND_DIR, "css")
    js_dir = os.path.join(FRONTEND_DIR, "js")
    if os.path.isdir(css_dir):
        app.mount("/css", StaticFiles(directory=css_dir), name="css")
    if os.path.isdir(js_dir):
        app.mount("/js", StaticFiles(directory=js_dir), name="js")


@app.on_event("startup")
async def preload_ai_models_in_background():
    """Start AI model initialization without blocking the API server.

    The API remains available immediately, so users can upload videos while
    the models are loading. Analysis workers wait for this background preload
    to finish before running inference.
    """
    logger.info("[STARTUP] Starting background AI model initialization...")
    threading.Thread(
        target=frame_processor.preload_models,
        name="netra-model-preloader",
        daemon=True,
    ).start()

    def _preload_face():
        try:
            face_reid.ensure_models()
            face_reid.reload_gallery(db_mod.list_poi_embeddings())
        except Exception:
            logger.exception("Face re-id preload failed")

    threading.Thread(target=_preload_face, name="netra-face-preloader", daemon=True).start()


@app.get("/")
def serve_frontend():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail=f"Frontend not found at {index_path}")
    return FileResponse(index_path, media_type="text/html")