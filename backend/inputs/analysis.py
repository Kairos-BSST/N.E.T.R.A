"""
inputs/analysis.py
------------------
Shared model-analysis placeholder endpoints (all input types feed here).
"""

from fastapi import APIRouter, HTTPException

import analysis_pipeline
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


@router.get("/videos")
def list_fetched_videos():
    """Every video ingested so far (Drive fetch + local upload)."""
    return state.all_fetched()
