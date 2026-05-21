import logging
from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from app.models import Lead, PipelineConfig
from app.utils.csv_parser import parse_upload
from app.services.pipeline import run_pipeline, LeadResult, PipelineResult
from app.services.excel_export import export_pipeline_results
from app.services import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["process"])


def _init_job(filename: str, leads: list[Lead], config: PipelineConfig) -> tuple[str | None, list[str] | None]:
    """Create job + leads in Supabase. Returns (job_id, lead_ids) or (None, None) on failure."""
    try:
        job = db.create_job(filename=filename, total_leads=len(leads), config=config)
        job_id = job["id"]
        db_leads = db.insert_leads(job_id, leads)
        db_lead_ids = [dl["id"] for dl in db_leads]
        return job_id, db_lead_ids
    except Exception as e:
        logger.warning(f"Supabase init failed, running without persistence: {e}")
        return None, None


def _get_research_record(cached: dict) -> dict:
    """Extract research record from Supabase join — handles both list and dict shapes."""
    rr = cached.get("research_results", {})
    if isinstance(rr, list):
        return rr[0] if rr else {}
    return rr


def _build_cached_result(lead: Lead, cached: dict) -> LeadResult:
    """Build a LeadResult from previously cached research data."""
    rr = _get_research_record(cached)
    research = {
        "company_snapshot": rr.get("company_snapshot", {}),
        "prospect_role": rr.get("prospect_role", {}),
        "pain_signals": rr.get("pain_signals", []),
        "confidence_score": rr.get("confidence_score"),
        "data_gaps": rr.get("data_gaps", []),
    }
    return LeadResult(
        lead=lead.model_dump(),
        research=research,
        source_urls=rr.get("source_urls", []),
        linkedin_data=rr.get("linkedin_data"),
        duration_s=0.0,
    )


def _dedup_leads(leads: list[Lead], db_lead_ids: list[str] | None) -> tuple[
    list[Lead], list[str] | None,  # new leads + their db IDs
    list[LeadResult],              # cached results
    int,                           # count of cached
]:
    """Split leads into new (need processing) and cached (fresh existing research).
    Dedup key: LinkedIn URL. Research older than cache_max_age_days is ignored."""
    try:
        linkedin_urls = [l.linkedin for l in leads if l.linkedin]
        existing = db.find_existing_research(linkedin_urls)
    except Exception as e:
        logger.warning(f"Dedup lookup failed, processing all leads: {e}")
        return leads, db_lead_ids, [], 0

    if not existing:
        return leads, db_lead_ids, [], 0

    new_leads = []
    new_db_ids = []
    cached_results = []

    for i, lead in enumerate(leads):
        if lead.linkedin and lead.linkedin in existing:
            cached_results.append(_build_cached_result(lead, existing[lead.linkedin]))
            # Mark this lead as completed in DB (it was cached)
            if db_lead_ids:
                try:
                    db.update_lead(db_lead_ids[i], status="completed")
                    # Copy research result for this new lead row
                    rr = _get_research_record(existing[lead.linkedin])
                    db.insert_research_result(
                        lead_id=db_lead_ids[i],
                        research={
                            "company_snapshot": rr.get("company_snapshot", {}),
                            "prospect_role": rr.get("prospect_role", {}),
                            "pain_signals": rr.get("pain_signals", []),
                            "confidence_score": rr.get("confidence_score"),
                            "data_gaps": rr.get("data_gaps", []),
                        },
                        source_urls=rr.get("source_urls", []),
                        linkedin_data=rr.get("linkedin_data"),
                    )
                except Exception:
                    pass
        else:
            new_leads.append(lead)
            if db_lead_ids:
                new_db_ids.append(db_lead_ids[i])

    return new_leads, new_db_ids if db_lead_ids else None, cached_results, len(cached_results)


async def _run_with_dedup(
    leads: list[Lead],
    config: PipelineConfig,
    job_id: str | None,
    db_lead_ids: list[str] | None,
) -> tuple[PipelineResult, int]:
    """Run pipeline with deduplication. Returns (result, cached_count)."""
    new_leads, new_db_ids, cached_results, cached_count = _dedup_leads(leads, db_lead_ids)

    if not new_leads:
        # All leads were cached
        pipeline_result = PipelineResult(
            job_id=job_id,
            total=len(leads),
            processed=len(leads),
            failed=0,
            results=cached_results,
            duration_s=0.0,
        )
        if job_id:
            try:
                db.update_job(job_id, status="completed", processed=len(leads), duration_s=0.0)
            except Exception:
                pass
        return pipeline_result, cached_count

    # Run pipeline for new leads only
    result = await run_pipeline(new_leads, config, job_id=job_id, db_lead_ids=new_db_ids)

    # Merge cached results into pipeline result
    result.results.extend(cached_results)
    result.total = len(leads)
    result.processed += cached_count

    # Update job with final counts
    if job_id:
        try:
            db.update_job(job_id, processed=result.processed, duration_s=result.duration_s)
        except Exception:
            pass

    return result, cached_count


@router.post("/process")
async def process_leads(
    file: UploadFile = File(..., description="CSV or Excel file with leads"),
    search: bool = Query(True, description="Run Serper web search"),
    extract: bool = Query(True, description="Run Jina content extraction"),
    synthesize: bool = Query(True, description="Run OpenAI research synthesis"),
    apollo: bool = Query(False, description="Run Apollo enrichment"),
    linkedin: bool = Query(False, description="Run Apify LinkedIn scraping (profile + posts)"),
):
    """End-to-end: upload CSV → pipeline → download Excel report."""

    # 1. Parse uploaded file
    try:
        leads, skipped = await parse_upload(file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing failed: {str(e)}")

    if not leads:
        raise HTTPException(
            status_code=400,
            detail=f"No valid leads found. {len(skipped)} rows skipped.",
        )

    # 2. Create job in Supabase
    config = PipelineConfig(
        search=search, extract=extract, synthesize=synthesize,
        apollo=apollo, linkedin=linkedin,
    )
    job_id, db_lead_ids = _init_job(file.filename or "upload.csv", leads, config)

    # 3. Run pipeline with dedup
    try:
        result, cached_count = await _run_with_dedup(leads, config, job_id, db_lead_ids)
    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        if job_id:
            try:
                db.update_job(job_id, status="failed")
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {type(e).__name__}: {str(e)}")

    # 4. Generate Excel
    buffer = export_pipeline_results(result)
    filename = file.filename.rsplit(".", 1)[0] if file.filename else "leads"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={filename}_research.xlsx",
        },
    )


@router.post("/process/json")
async def process_leads_json(
    file: UploadFile = File(..., description="CSV or Excel file with leads"),
    search: bool = Query(True),
    extract: bool = Query(True),
    synthesize: bool = Query(True),
    apollo: bool = Query(False),
    linkedin: bool = Query(False),
):
    """Same as /process but returns JSON instead of Excel. Useful for integrations."""

    try:
        leads, skipped = await parse_upload(file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing failed: {str(e)}")

    if not leads:
        raise HTTPException(
            status_code=400,
            detail=f"No valid leads found. {len(skipped)} rows skipped.",
        )

    config = PipelineConfig(
        search=search, extract=extract, synthesize=synthesize,
        apollo=apollo, linkedin=linkedin,
    )
    job_id, db_lead_ids = _init_job(file.filename or "upload.csv", leads, config)

    try:
        result, cached_count = await _run_with_dedup(leads, config, job_id, db_lead_ids)
    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        if job_id:
            try:
                db.update_job(job_id, status="failed")
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {type(e).__name__}: {str(e)}")

    return {
        "job_id": result.job_id,
        "summary": {
            "total": result.total,
            "processed": result.processed,
            "failed": result.failed,
            "cached": cached_count,
            "skipped": len(skipped),
            "duration_s": result.duration_s,
        },
        "skipped_rows": skipped,
        "results": [
            {
                "lead": r.lead,
                "linkedin_data": r.linkedin_data,
                "research": r.research,
                "source_urls": r.source_urls,
                "error": r.error,
                "duration_s": r.duration_s,
            }
            for r in result.results
        ],
    }
