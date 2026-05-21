from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse
from app.services import db
from app.services.pipeline import LeadResult, PipelineResult
from app.services.excel_export import export_pipeline_results
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    limit: int = Query(50, le=100),
    user_id: str | None = Depends(get_current_user),
):
    """List jobs for the authenticated user, newest first."""
    return db.list_jobs(user_id=user_id, limit=limit)


@router.get("/{job_id}")
async def get_job(job_id: str, user_id: str | None = Depends(get_current_user)):
    """Get job details including lead count summary."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user_id and job.get("user_id") and job["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/leads")
async def get_job_leads(job_id: str, user_id: str | None = Depends(get_current_user)):
    """Get all leads with research results for a job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user_id and job.get("user_id") and job["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    leads = db.get_leads_for_job(job_id)
    return {"job": job, "leads": leads}


@router.get("/{job_id}/export")
async def export_job(job_id: str, user_id: str | None = Depends(get_current_user)):
    """Re-generate and download the Excel report for a completed job."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user_id and job.get("user_id") and job["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail=f"Job is still {job['status']}")

    leads = db.get_leads_for_job(job_id)

    lead_results = []
    for lead_row in leads:
        research_data = lead_row.get("research_results")
        if isinstance(research_data, list):
            rr = research_data[0] if research_data else {}
        elif isinstance(research_data, dict):
            rr = research_data
        else:
            rr = {}

        research = {}
        if rr:
            research = {
                "company_snapshot": rr.get("company_snapshot", {}),
                "prospect_role": rr.get("prospect_role", {}),
                "pain_signals": rr.get("pain_signals", []),
                "confidence_score": rr.get("confidence_score"),
                "data_gaps": rr.get("data_gaps", []),
            }

        lead_results.append(LeadResult(
            lead={
                "name": lead_row["name"],
                "company": lead_row["company"],
                "email": lead_row.get("email"),
                "linkedin": lead_row.get("linkedin_url"),
            },
            research=research if research.get("company_snapshot") else None,
            source_urls=rr.get("source_urls", []) if rr else [],
            linkedin_data=rr.get("linkedin_data") if rr else None,
            jina={"extracted": len(rr.get("source_urls", []))} if rr else {},
            error=lead_row.get("error"),
            duration_s=lead_row.get("duration_s", 0) or 0,
        ))

    pipeline_result = PipelineResult(
        job_id=job_id,
        total=job["total_leads"],
        processed=job["processed"],
        failed=job["failed"],
        results=lead_results,
        duration_s=job.get("duration_s", 0) or 0,
    )

    buffer = export_pipeline_results(pipeline_result)
    filename = job.get("filename", "leads").rsplit(".", 1)[0]

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={filename}_research.xlsx",
        },
    )
