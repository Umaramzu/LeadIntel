import asyncio
import time
import logging
from typing import Callable
from urllib.parse import urlparse
from dataclasses import dataclass, field
from app.models import Lead, PipelineConfig
from app.services.serper import search_lead
from app.services.jina import extract_lead_content
from app.services.openai_synth import synthesize_lead
from app.services.apify import scrape_linkedin

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LeadResult:
    lead: dict
    db_lead_id: str | None = None
    serper: dict | None = None
    jina: dict | None = None
    linkedin_data: dict | None = None
    research: dict | None = None
    source_urls: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class PipelineResult:
    job_id: str | None = None
    total: int = 0
    processed: int = 0
    failed: int = 0
    results: list[LeadResult] = field(default_factory=list)
    duration_s: float = 0.0


def _persist_lead_result(lead_result: LeadResult):
    """Write lead result to Supabase. Non-blocking — errors are logged, not raised."""
    if not lead_result.db_lead_id:
        return
    try:
        from app.services.db import update_lead, insert_research_result

        status = "failed" if lead_result.error else "completed"
        update_lead(
            lead_result.db_lead_id,
            status=status,
            error=lead_result.error,
            duration_s=lead_result.duration_s,
        )

        if lead_result.research:
            insert_research_result(
                lead_id=lead_result.db_lead_id,
                research=lead_result.research,
                source_urls=lead_result.source_urls,
                linkedin_data=lead_result.linkedin_data,
            )
    except Exception as e:
        logger.warning(f"Failed to persist lead {lead_result.db_lead_id}: {e}")


async def _process_one_lead(
    lead: Lead,
    config: PipelineConfig,
    semaphore: asyncio.Semaphore,
    db_lead_id: str | None = None,
) -> LeadResult:
    """Run enabled pipeline steps for a single lead. Errors are captured, never raised."""
    async with semaphore:
        start = time.monotonic()
        result = LeadResult(lead=lead.model_dump(), db_lead_id=db_lead_id)

        # Mark lead as processing in DB
        if db_lead_id:
            try:
                from app.services.db import update_lead
                update_lead(db_lead_id, status="processing")
            except Exception:
                pass

        try:
            serper_results = None
            jina_extraction = None
            linkedin_data = None

            # Step 1: Serper search
            if config.search:
                serper_results = await search_lead(lead.name, lead.company)
                result.serper = {
                    "total_results": sum(
                        len(v.get("results", []))
                        for k, v in serper_results.items()
                        if not k.startswith("_")
                    ),
                    "queries": [k for k in serper_results.keys() if not k.startswith("_")],
                    "target_domain": serper_results.get("_meta", {}).get("target_domain"),
                }

            # Step 2: Jina extraction (requires Serper results)
            if config.extract and serper_results:
                try:
                    jina_extraction = await extract_lead_content(
                        serper_results, lead.name, lead.company
                    )
                    result.jina = {
                        "total_urls_found": jina_extraction["total_urls_found"],
                        "relevant_urls": jina_extraction["relevant_urls"],
                        "extracted": jina_extraction["extracted"],
                        "failed": jina_extraction["failed"],
                    }
                    seen_domains = set()
                    for e in jina_extraction["extractions"]:
                        if e.get("error") or not e.get("url"):
                            continue
                        domain = urlparse(e["url"]).netloc
                        if domain not in seen_domains:
                            seen_domains.add(domain)
                            result.source_urls.append(e["url"])
                        if len(result.source_urls) >= 2:
                            break
                except Exception as jina_err:
                    logger.error(f"[{lead.name}] Jina FAILED: {type(jina_err).__name__}: {jina_err}")

            # Step 3: LinkedIn scraping via Apify (optional, requires LinkedIn URL)
            if config.linkedin and lead.linkedin:
                try:
                    linkedin_data = await scrape_linkedin(lead.linkedin)
                    result.linkedin_data = linkedin_data
                except Exception as li_err:
                    logger.error(f"[{lead.name}] LinkedIn FAILED: {type(li_err).__name__}: {li_err}")

            # Step 4: OpenAI synthesis (requires Serper results + Jina extractions)
            if config.synthesize and serper_results:
                extractions = (
                    jina_extraction["extractions"] if jina_extraction else []
                )
                relevant_snippets = (
                    jina_extraction.get("relevant_results", []) if jina_extraction else []
                )
                synthesis = await synthesize_lead(
                    name=lead.name,
                    company=lead.company,
                    email=lead.email,
                    linkedin=lead.linkedin,
                    serper_results=serper_results,
                    jina_extractions=extractions,
                    linkedin_data=linkedin_data,
                    relevant_snippets=relevant_snippets,
                )
                result.research = synthesis["research"]

        except Exception as e:
            result.error = f"{type(e).__name__}: {str(e)[:300]}"

        result.duration_s = round(time.monotonic() - start, 2)

        # Persist to Supabase
        _persist_lead_result(result)

        return result


async def run_pipeline(
    leads: list[Lead],
    config: PipelineConfig | None = None,
    on_progress: Callable | None = None,
    job_id: str | None = None,
    db_lead_ids: list[str] | None = None,
) -> PipelineResult:
    """Process a batch of leads through the enabled pipeline steps.

    Args:
        leads: List of Lead objects to process.
        config: Which steps to run. Defaults to search+extract+synthesize.
        on_progress: Optional callback(processed, failed, total) called after each lead.
        job_id: Supabase job ID for persistent tracking. None = no persistence.
        db_lead_ids: Supabase lead IDs matching the leads list order. None = no persistence.
    """
    if config is None:
        config = PipelineConfig()

    pipeline_start = time.monotonic()
    semaphore = asyncio.Semaphore(get_settings().pipeline_max_concurrency)

    tasks = [
        _process_one_lead(
            lead, config, semaphore,
            db_lead_id=db_lead_ids[i] if db_lead_ids else None,
        )
        for i, lead in enumerate(leads)
    ]

    pipeline_result = PipelineResult(job_id=job_id, total=len(leads))

    for coro in asyncio.as_completed(tasks):
        lead_result = await coro
        pipeline_result.results.append(lead_result)

        if lead_result.error:
            pipeline_result.failed += 1
        else:
            pipeline_result.processed += 1

        # Update job progress in DB
        if job_id:
            try:
                from app.services.db import update_job
                update_job(job_id, processed=pipeline_result.processed, failed=pipeline_result.failed)
            except Exception:
                pass

        if on_progress:
            on_progress(
                pipeline_result.processed,
                pipeline_result.failed,
                pipeline_result.total,
            )

    pipeline_result.duration_s = round(time.monotonic() - pipeline_start, 2)

    # Final job update
    if job_id:
        try:
            from app.services.db import update_job
            status = "completed" if pipeline_result.failed < pipeline_result.total else "failed"
            update_job(job_id, status=status, duration_s=pipeline_result.duration_s)
        except Exception as e:
            logger.warning(f"Failed to update job {job_id}: {e}")

    return pipeline_result
