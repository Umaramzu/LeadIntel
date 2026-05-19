from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from app.models import PipelineConfig
from app.utils.csv_parser import parse_upload
from app.services.pipeline import run_pipeline
from app.services.excel_export import export_pipeline_results

router = APIRouter(prefix="/api", tags=["process"])


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

    # 2. Run pipeline
    config = PipelineConfig(
        search=search, extract=extract, synthesize=synthesize,
        apollo=apollo, linkedin=linkedin,
    )

    try:
        result = await run_pipeline(leads, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")

    # 3. Generate Excel
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

    try:
        result = await run_pipeline(leads, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")

    return {
        "summary": {
            "total": result.total,
            "processed": result.processed,
            "failed": result.failed,
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
