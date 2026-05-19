import asyncio
import time
from typing import Callable
from urllib.parse import urlparse
from dataclasses import dataclass, field
from app.models import Lead, PipelineConfig
from app.services.serper import search_lead
from app.services.jina import extract_lead_content
from app.services.openai_synth import synthesize_lead
from app.services.apify import scrape_linkedin

MAX_CONCURRENCY = 5


@dataclass
class LeadResult:
    lead: dict
    serper: dict | None = None
    jina: dict | None = None
    linkedin_data: dict | None = None
    research: dict | None = None
    source_urls: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class PipelineResult:
    total: int = 0
    processed: int = 0
    failed: int = 0
    results: list[LeadResult] = field(default_factory=list)
    duration_s: float = 0.0


async def _process_one_lead(
    lead: Lead,
    config: PipelineConfig,
    semaphore: asyncio.Semaphore,
) -> LeadResult:
    """Run enabled pipeline steps for a single lead. Errors are captured, never raised."""
    async with semaphore:
        start = time.monotonic()
        result = LeadResult(lead=lead.model_dump())

        try:
            serper_results = None
            jina_extraction = None
            linkedin_data = None

            # Step 1: Serper search
            if config.search:
                serper_results = await search_lead(lead.name, lead.company)
                result.serper = {
                    "total_results": sum(
                        len(r.get("results", [])) for r in serper_results.values()
                    ),
                    "queries": list(serper_results.keys()),
                }

            # Step 2: Jina extraction (requires Serper results)
            if config.extract and serper_results:
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

            # Step 3: LinkedIn scraping via Apify (optional, requires LinkedIn URL)
            if config.linkedin and lead.linkedin:
                try:
                    linkedin_data = await scrape_linkedin(lead.linkedin)
                    result.linkedin_data = linkedin_data
                except Exception:
                    pass

            # Step 4: OpenAI synthesis (requires Serper results + Jina extractions)
            if config.synthesize and serper_results:
                extractions = (
                    jina_extraction["extractions"] if jina_extraction else []
                )
                synthesis = await synthesize_lead(
                    name=lead.name,
                    company=lead.company,
                    email=lead.email,
                    linkedin=lead.linkedin,
                    serper_results=serper_results,
                    jina_extractions=extractions,
                    linkedin_data=linkedin_data,
                )
                result.research = synthesis["research"]

        except Exception as e:
            result.error = f"{type(e).__name__}: {str(e)[:300]}"

        result.duration_s = round(time.monotonic() - start, 2)
        return result


async def run_pipeline(
    leads: list[Lead],
    config: PipelineConfig | None = None,
    on_progress: Callable | None = None,
) -> PipelineResult:
    """Process a batch of leads through the enabled pipeline steps.

    Args:
        leads: List of Lead objects to process.
        config: Which steps to run. Defaults to search+extract+synthesize.
        on_progress: Optional callback(processed, failed, total) called after each lead.
    """
    if config is None:
        config = PipelineConfig()

    pipeline_start = time.monotonic()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    tasks = [
        _process_one_lead(lead, config, semaphore)
        for lead in leads
    ]

    pipeline_result = PipelineResult(total=len(leads))

    for coro in asyncio.as_completed(tasks):
        lead_result = await coro
        pipeline_result.results.append(lead_result)

        if lead_result.error:
            pipeline_result.failed += 1
        else:
            pipeline_result.processed += 1

        if on_progress:
            on_progress(
                pipeline_result.processed,
                pipeline_result.failed,
                pipeline_result.total,
            )

    pipeline_result.duration_s = round(time.monotonic() - pipeline_start, 2)
    return pipeline_result
