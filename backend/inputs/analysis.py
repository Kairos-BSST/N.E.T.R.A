"""
inputs/analysis.py
------------------
Shared model-analysis placeholder endpoints (all input types feed here).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import analysis_pipeline
import report_pdf
from deps import state

router = APIRouter(tags=["analysis"])


@router.get("/analysis/jobs")
def list_analysis_jobs():
    return {"jobs": analysis_pipeline.list_jobs()}


@router.get("/analysis/jobs/{job_id}")
def get_analysis_job(job_id: str):
    job = analysis_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    return job


@router.get("/analysis/jobs/{job_id}/report")
def get_analysis_report(job_id: str):
    """
    Report-ready view of one analysis job: source info, summary counters,
    and the full chronological, timestamped event log (with evidence
    thumbnail URLs) — everything needed to render or export a forensic
    review report for this video.
    """
    job = analysis_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")

    events = job.get("events", [])

    return {
        "job_id": job.get("job_id"),
        "source": job.get("source"),
        "original_name": job.get("original_name"),
        "status": job.get("status"),
        "message": job.get("message"),
        "error": job.get("error"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "video_info": job.get("video_info"),
        "summary": job.get("result"),
        "event_count": len(events),
        "events": sorted(events, key=lambda e: e.get("video_time_seconds", 0)),
    }


@router.get("/analysis/jobs/{job_id}/report/download")
def download_analysis_report(job_id: str):
    """
    Generate (or regenerate) and return the forensic PDF report for this
    job — source info, summary counters, and every logged event with its
    timestamp, location, and evidence thumbnail.
    """
    job = analysis_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")

    pdf_path = report_pdf.build_report_pdf(job)

    safe_name = (job.get("original_name") or job_id).rsplit(".", 1)[0]
    filename = f"NETRA_report_{safe_name}.pdf"

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/videos")
def list_fetched_videos():
    """Every video ingested so far (Drive fetch + local upload)."""
    return state.all_fetched()