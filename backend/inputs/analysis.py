from __future__ import annotations
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import analysis_pipeline
import database
import report_pdf
from auth import administrator, can_access_job, current_user
from config import Config
from deps import state

router = APIRouter(tags=["analysis"])

class OperatorCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)

def _authorized_job(job_id: str, user: dict) -> dict:
    job = analysis_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    if not can_access_job(user, job):
        raise HTTPException(status_code=403, detail="You do not have access to this analysis.")
    return job

def _secure_event(event: dict, job_id: str) -> dict:
    ev = dict(event)
    url = ev.get("snapshot_url")
    if isinstance(url, str) and url.startswith(f"/snapshots/{job_id}/"):
        ev["snapshot_url"] = url.replace(f"/snapshots/{job_id}/", f"/evidence/snapshots/{job_id}/", 1)
    return ev

@router.get("/analysis/jobs")
def list_analysis_jobs(user=Depends(current_user)):
    if user.get("role") == "administrator":
        return {"jobs": database.list_jobs(limit=100)}
    return database.history_jobs(user_id=user["id"], is_admin=False, page=1, page_size=100)

@router.get("/analysis/jobs/{job_id}")
def get_analysis_job(job_id: str, user=Depends(current_user)):
    return _authorized_job(job_id, user)

@router.post("/analysis/jobs/{job_id}/stop")
def stop_analysis_job(job_id: str, user=Depends(current_user)):
    job = _authorized_job(job_id, user)
    status = (job.get("status") or "").lower()
    if status in {"completed", "failed"}:
        return {"status": job.get("status"), "job": job}
    try:
        updated = analysis_pipeline.stop_analysis(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    database.record_audit(
        user["id"],
        "ANALYSIS_STOPPED",
        job_id=job_id,
        resource_type="analysis",
        resource_id=job_id,
        details={"source": updated.get("source"), "status": updated.get("status")},
    )
    return {"status": "stopped", "job": updated}

@router.get("/analysis/jobs/{job_id}/report")
def get_analysis_report(job_id: str, user=Depends(current_user)):
    job = _authorized_job(job_id, user)
    database.record_audit(user["id"], "REPORT_VIEWED", job_id=job_id, resource_type="report", resource_id=job_id)
    events = [_secure_event(e, job_id) for e in job.get("events", [])]
    return {
        "job_id": job.get("job_id"), "source": job.get("source"),
        "original_name": job.get("original_name"), "status": job.get("status"),
        "message": job.get("message"), "error": job.get("error"),
        "queued_at": job.get("queued_at"), "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"), "video_info": job.get("video_info"),
        "summary": job.get("result"),
        "annotated_video_url": (
            f"/evidence/annotated/{job_id}"
            if ((job.get("result") or {}).get("annotated_video_url") or job.get("annotated_video_url"))
            else None
        ),
        "plates_found": (job.get("result") or {}).get("plates_found") or [],
        "event_count": len(events),
        "events": sorted(events, key=lambda e: e.get("video_time_seconds", 0)),
        "evidence": database.evidence_for_job(job_id),
    }

@router.get("/analysis/jobs/{job_id}/report/download")
def download_analysis_report(job_id: str, user=Depends(current_user)):
    job = _authorized_job(job_id, user)
    pdf_path = report_pdf.build_report_pdf(job)
    database.record_audit(user["id"], "REPORT_DOWNLOADED", job_id=job_id, resource_type="report", resource_id=job_id, details={"sha256": database.hash_file(pdf_path)})
    safe_name = (job.get("original_name") or job_id).rsplit(".", 1)[0]
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"NETRA_report_{safe_name}.pdf")

@router.get("/evidence/snapshots/{job_id}/{filename}")
def get_snapshot(job_id: str, filename: str, user=Depends(current_user)):
    job = _authorized_job(job_id, user)
    path = os.path.join(os.path.abspath(Config.SNAPSHOT_DIR), job_id, filename)
    if os.path.basename(path) != filename or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Evidence snapshot not found.")
    database.record_audit(user["id"], "EVIDENCE_VIEWED", job_id=job_id, resource_type="snapshot", resource_id=filename)
    return FileResponse(path, media_type="image/jpeg")

@router.get("/evidence/annotated/{job_id}")
def get_annotated_video(job_id: str, user=Depends(current_user)):
    _authorized_job(job_id, user)
    path = os.path.join(os.path.abspath(Config.SNAPSHOT_DIR), job_id, "annotated.mp4")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Annotated video not found.")
    database.record_audit(user["id"], "EVIDENCE_VIEWED", job_id=job_id, resource_type="annotated_video", resource_id=os.path.basename(path))
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

@router.get("/evidence/clips/{job_id}/{filename}")
def get_clip(job_id: str, filename: str, user=Depends(current_user)):
    _authorized_job(job_id, user)
    base = os.path.abspath(Config.SNAPSHOT_DIR)
    path = os.path.join(base, job_id, "clips", filename)
    if os.path.basename(path) != filename or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Evidence clip not found.")
    database.record_audit(user["id"], "EVIDENCE_DOWNLOADED", job_id=job_id, resource_type="clip", resource_id=filename)
    return FileResponse(path, media_type="video/mp4", filename=filename)

@router.get("/history")
def history(
    search: str = "", source: str = "", status: str = "", event_type: str = "",
    date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 25,
    user=Depends(current_user),
):
    return database.history_jobs(
        user_id=None if user.get("role") == "administrator" else user.get("id"), is_admin=user.get("role") == "administrator",
        search=search.strip(), source=source, status=status, event_type=event_type,
        date_from=date_from, date_to=date_to, page=max(1, page), page_size=max(1, min(page_size, 100)),
    )

@router.get("/admin/audit")
def admin_audit(
    search: str = "", username: str = "", action: str = "", date_from: str = "", date_to: str = "",
    page: int = 1, page_size: int = 50, user=Depends(administrator),
):
    return database.audit_history(
        search=search.strip(), username=username, action=action,
        date_from=date_from, date_to=date_to, page=max(1, page), page_size=max(1, min(page_size, 100)),
        allowed_actions=(
            "LOGIN", "LOGOUT", "VIDEO_UPLOADED", "DRIVE_FETCHED", "CCTV_CONNECTED",
            "ANALYSIS_QUEUED", "ANALYSIS_COMPLETED", "ANALYSIS_FAILED", "REPORT_DOWNLOADED", "OPERATOR_CREATED",
        ),
    )

@router.get("/admin/scans")
def admin_scans(
    search: str = "", source: str = "", status: str = "",
    date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 25,
    user=Depends(administrator),
):
    return database.history_jobs(
        user_id=None, is_admin=True, search=search.strip(), source=source, status=status,
        date_from=date_from, date_to=date_to, page=max(1, page), page_size=max(1, min(page_size, 100)),
    )

@router.get("/admin/users")
def admin_users(user=Depends(administrator)):
    return {"users": database.users_for_filter()}

@router.post("/admin/operators")
def add_operator(body: OperatorCreate, user=Depends(administrator)):
    try:
        operator = database.create_user(body.username, body.password, role="operator")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.record_audit(
        user["id"],
        "OPERATOR_CREATED",
        resource_type="user",
        resource_id=str(operator["id"]),
        details={"username": operator["username"], "role": "operator"},
    )
    return {"operator": operator}

@router.get("/videos")
def list_fetched_videos(user=Depends(current_user)):
    database.record_audit(user["id"], "VIDEO_LIST_VIEWED")
    return state.all_fetched()