from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.models import Lead, PipelineConfig
from app.services.serper import search_lead
from app.services.jina import extract_lead_content
from app.services.openai_synth import synthesize_lead
from app.services.apify import scrape_linkedin_profile, scrape_linkedin_posts, scrape_linkedin, _run_actor, PROFILE_ACTOR_ID, POSTS_ACTOR_ID
from app.services.pipeline import run_pipeline
from app.services.excel_export import export_pipeline_results

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.get("/serper")
async def debug_serper(
    name: str = Query(..., description="Person name"),
    company: str = Query(..., description="Company name"),
):
    try:
        results = await search_lead(name, company)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Serper search failed: {str(e)}")

    total_results = sum(len(r.get("results", [])) for r in results.values())
    errors = [k for k, v in results.items() if "error" in v]
    return {
        "lead": {"name": name, "company": company},
        "total_results": total_results,
        "errors": errors,
        "search_results": results,
    }


@router.get("/jina")
async def debug_jina(
    name: str = Query(..., description="Person name"),
    company: str = Query(..., description="Company name"),
):
    """Serper search → pre-filter → Jina extraction."""
    try:
        serper_results = await search_lead(name, company)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Serper search failed: {str(e)}")

    try:
        extraction = await extract_lead_content(serper_results, name, company)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Jina extraction failed: {str(e)}")

    return {
        "lead": {"name": name, "company": company},
        "pre_filter_stats": {
            "total_urls_found": extraction["total_urls_found"],
            "relevant_after_filter": extraction["relevant_urls"],
            "filtered_out_count": len(extraction["filtered_out"]),
            "filtered_out_urls": extraction["filtered_out"],
        },
        "extraction_stats": {
            "extracted": extraction["extracted"],
            "failed": extraction["failed"],
        },
        "extractions": [
            {
                "url": e["url"],
                "title": e["title"],
                "source_query": e.get("source_query", ""),
                "content_preview": (
                    e["content"][:300] + "..."
                    if len(e["content"]) > 300
                    else e["content"]
                ),
                "content_length": len(e["content"]),
                "error": e["error"],
            }
            for e in extraction["extractions"]
        ],
    }


@router.get("/synthesize")
async def debug_synthesize(
    name: str = Query(..., description="Person name"),
    company: str = Query(..., description="Company name"),
    email: str = Query(None, description="Email (optional)"),
    linkedin: str = Query(None, description="LinkedIn URL (optional)"),
):
    """Full chain: Serper → pre-filter → Jina → OpenAI synthesis."""
    # Step 1: Serper search
    try:
        serper_results = await search_lead(name, company)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Serper failed: {str(e)}")

    # Step 2: Jina extraction (includes pre-filter)
    try:
        extraction = await extract_lead_content(serper_results, name, company)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Jina failed: {str(e)}")

    # Step 3: OpenAI synthesis
    try:
        synthesis = await synthesize_lead(
            name=name,
            company=company,
            email=email,
            linkedin=linkedin,
            serper_results=serper_results,
            jina_extractions=extraction["extractions"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {str(e)}")

    return {
        "lead": {
            "name": name,
            "company": company,
            "email": email,
            "linkedin": linkedin,
        },
        "pipeline_stats": {
            "serper_results": sum(
                len(r.get("results", [])) for r in serper_results.values()
            ),
            "relevant_after_filter": extraction["relevant_urls"],
            "urls_extracted": extraction["extracted"],
            "urls_failed": extraction["failed"],
        },
        "research": synthesis["research"],
        "token_usage": synthesis["usage"],
    }


@router.get("/linkedin/raw")
async def debug_linkedin_raw(
    url: str = Query(..., description="LinkedIn profile URL"),
):
    """Return RAW Apify response to inspect actual JSON structure."""
    try:
        raw_items = await _run_actor(PROFILE_ACTOR_ID, {"profileUrls": [url]})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Apify raw call failed: {str(e)}")

    return {
        "linkedin_url": url,
        "total_items": len(raw_items),
        "raw_keys": list(raw_items[0].keys()) if raw_items else [],
        "raw_data": raw_items,
    }


@router.get("/linkedin/profile")
async def debug_linkedin_profile(
    url: str = Query(..., description="LinkedIn profile URL"),
):
    """Test Apify Profile Scraper in isolation."""
    try:
        profile = await scrape_linkedin_profile(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Apify profile scrape failed: {str(e)}")

    return {"linkedin_url": url, "profile": profile}


@router.get("/linkedin/posts")
async def debug_linkedin_posts(
    url: str = Query(..., description="LinkedIn profile URL"),
):
    """Test Apify Posts Scraper in isolation."""
    try:
        posts = await scrape_linkedin_posts(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Apify posts scrape failed: {str(e)}")

    return {"linkedin_url": url, "total_posts": len(posts), "posts": posts}


@router.get("/linkedin")
async def debug_linkedin_full(
    url: str = Query(..., description="LinkedIn profile URL"),
):
    """Test both Apify scrapers (profile + posts) concurrently."""
    try:
        data = await scrape_linkedin(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Apify LinkedIn scrape failed: {str(e)}")

    return {
        "linkedin_url": url,
        "profile": data["profile"],
        "total_posts": len(data["posts"]),
        "posts": data["posts"],
    }


class PipelineRequest(BaseModel):
    leads: list[Lead]
    config: PipelineConfig = PipelineConfig()


@router.post("/pipeline")
async def debug_pipeline(request: PipelineRequest):
    """Run the full pipeline on a batch of leads. Use for testing before CSV integration."""
    if not request.leads:
        raise HTTPException(status_code=400, detail="No leads provided.")
    if len(request.leads) > 10:
        raise HTTPException(
            status_code=400,
            detail="Debug endpoint limited to 10 leads. Use /api/process for larger batches.",
        )

    try:
        result = await run_pipeline(request.leads, request.config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")

    return {
        "summary": {
            "total": result.total,
            "processed": result.processed,
            "failed": result.failed,
            "duration_s": result.duration_s,
        },
        "results": [
            {
                "lead": r.lead,
                "serper_stats": r.serper,
                "jina_stats": r.jina,
                "linkedin_data": r.linkedin_data,
                "research": r.research,
                "source_urls": r.source_urls,
                "error": r.error,
                "duration_s": r.duration_s,
            }
            for r in result.results
        ],
    }


@router.post("/pipeline/export")
async def debug_pipeline_export(request: PipelineRequest):
    """Run pipeline + return Excel file download."""
    if not request.leads:
        raise HTTPException(status_code=400, detail="No leads provided.")
    if len(request.leads) > 10:
        raise HTTPException(status_code=400, detail="Debug endpoint limited to 10 leads.")

    try:
        result = await run_pipeline(request.leads, request.config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")

    buffer = export_pipeline_results(result)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=leadintel_research.xlsx"},
    )
