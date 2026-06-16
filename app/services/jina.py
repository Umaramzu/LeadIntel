import asyncio
import logging
import re
import httpx
from urllib.parse import urlparse
from app.config import get_settings

logger = logging.getLogger(__name__)

JINA_READER_URL = "https://r.jina.ai"

# Domains that waste extraction slots — social media (login-walled),
# lead scraper aggregators (shallow mirrored data)
LOW_VALUE_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "pinterest.com", "linkedin.com", "yelp.com",
    "zoominfo.com", "rocketreach.co", "prospeo.io", "lusha.com",
    "signalhire.com", "leadiq.com", "apollo.io",
}


COMPANY_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "plc",
    "corp", "corporation", "co", "company", "group", "holdings",
}

GENERIC_BUSINESS_WORDS = {
    "services", "solutions", "consulting", "management", "partners",
    "associates", "global", "international", "digital", "tech",
    "technology", "systems", "ventures", "enterprises", "capital",
    "residential", "commercial", "property", "properties", "estate",
    "real", "exchange", "housing", "homes", "living", "national",
    "professional", "general", "first", "united", "modern", "premier",
    "advanced", "strategic", "creative", "smart", "innovative",
}


def _is_company_match(company_lower: str, text: str) -> bool:
    """Company name matching with false-positive protection for generic names.

    Tier 1: Full core name appears as phrase → definitive match
    Tier 2: Any distinctive (non-generic) word from name appears → match
    Tier 3: ALL generic words co-occur (3+ words required) → match
            Disabled for 2-word all-generic names because scattered
            co-occurrence of 2 common words is too unreliable
            (e.g., "exchange" + "residential" matches any real estate article)
    """
    core_parts = [
        p for p in company_lower.split()
        if p not in COMPANY_SUFFIXES and len(p) > 2
    ]
    core_name = " ".join(core_parts)

    if core_name and core_name in text:
        return True

    distinctive = [p for p in core_parts if p not in GENERIC_BUSINESS_WORDS]
    if distinctive and any(p in text for p in distinctive):
        return True

    if not distinctive and len(core_parts) >= 3 and all(p in text for p in core_parts):
        return True

    return False


def _is_name_match(name_lower: str, text: str) -> bool:
    """Check if a person's name appears in text using word-boundary matching.

    Requires ALL name parts to appear as whole words (not substrings).
    This prevents "gupta" matching any Gupta, or "li" matching inside
    "published". Both first AND last name must be present.
    """
    if name_lower in text:
        return True
    name_parts = [p for p in name_lower.split() if len(p) > 1]
    if not name_parts:
        return False
    return all(
        re.search(r'\b' + re.escape(p) + r'\b', text)
        for p in name_parts
    )


def filter_relevant_results(
    serper_results: dict, name: str, company: str
) -> list[dict]:
    """Pre-filter Serper results using query-aware matching.

    Different queries need different matching strategies:
    - company_* queries (services, news, reviews): require company_match.
      These queries search for company info — name matching is irrelevant
      and only adds false positives.
    - person_background query: require BOTH company_match AND name_match.
      This query searches for a specific person at a specific company —
      a page about a different person (same name, different company) or
      a company-only page should not pass.

    Name matching uses word-boundary regex with all() to prevent single
    common surname from matching the world.
    """
    name_lower = name.lower()
    company_lower = company.lower()

    relevant = []
    seen_urls = set()

    for query_key, query_data in serper_results.items():
        is_person_query = query_key == "person_background"

        for result in query_data.get("results", []):
            url = result.get("link", "")
            if not url or url in seen_urls:
                continue

            netloc = urlparse(url).netloc.lower()
            if any(d in netloc for d in LOW_VALUE_DOMAINS):
                continue

            title = result.get("title", "").lower()
            snippet = result.get("snippet", "").lower()
            text = f"{title} {snippet}"

            company_match = _is_company_match(company_lower, text)

            if is_person_query:
                name_match = _is_name_match(name_lower, text)
                passes = company_match and name_match
            else:
                passes = company_match

            if passes:
                seen_urls.add(url)
                relevant.append({
                    "url": url,
                    "title": result.get("title", ""),
                    "snippet": result.get("snippet", ""),
                    "source_query": query_key,
                })

    return relevant


def _parse_jina_response(resp: httpx.Response) -> dict:
    """Parse Jina response — handles both JSON and plain text formats."""
    content_type = resp.headers.get("content-type", "")

    # Try JSON first
    if "application/json" in content_type:
        data = resp.json()
        # Handle wrapped response: {"code": 200, "data": {...}}
        if "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        return {
            "title": data.get("title", ""),
            "content": data.get("content", ""),
            "description": data.get("description", ""),
            "tokens": data.get("usage", {}).get("tokens", 0),
        }

    # Fallback: plain text / markdown response
    text = resp.text.strip()
    title = ""
    content = text

    # Jina plain text format has "Title: ..." and "Markdown Content:" sections
    if text.startswith("Title:"):
        lines = text.split("\n")
        title = lines[0].replace("Title:", "").strip()
        # Find content after metadata headers
        content_start = 0
        for i, line in enumerate(lines):
            if line.startswith("Markdown Content:"):
                content_start = i + 1
                break
            elif line.strip() == "" and i > 3:
                content_start = i + 1
                break
        content = "\n".join(lines[content_start:]).strip()

    return {
        "title": title,
        "content": content,
        "description": "",
        "tokens": 0,
    }


async def extract_url(url: str, api_key: str, client: httpx.AsyncClient) -> dict:
    """Extract clean content from a single URL via Jina Reader."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "X-Retain-Images": "none",
    }

    try:
        resp = await client.get(f"{JINA_READER_URL}/{url}", headers=headers)
        resp.raise_for_status()

        parsed = _parse_jina_response(resp)
        content = parsed["content"]

        max_len = get_settings().jina_max_content_length
        if len(content) > max_len:
            content = content[:max_len] + "\n\n[...truncated]"

        return {
            "url": url,
            "title": parsed["title"],
            "content": content,
            "description": parsed["description"],
            "tokens": parsed["tokens"],
            "error": None,
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"[jina] {url[:80]} | HTTP {e.response.status_code}")
        return {
            "url": url,
            "title": "",
            "content": "",
            "description": "",
            "tokens": 0,
            "error": f"HTTP {e.response.status_code}",
        }
    except (httpx.RequestError, Exception) as e:
        err_msg = str(e)[:200] or f"{type(e).__name__} (no message)"
        logger.error(f"[jina] {url[:80]} | {type(e).__name__}: {err_msg}")
        return {
            "url": url,
            "title": "",
            "content": "",
            "description": "",
            "tokens": 0,
            "error": err_msg,
        }


def _is_useful_content(title: str, content: str) -> bool:
    """Detect auth walls, bot challenges, and empty pages after extraction."""
    title_lower = (title or "").lower()
    content_stripped = (content or "").strip()

    if "sign up | linkedin" in title_lower or "join linkedin" in title_lower:
        return False
    if "just a moment" in title_lower or "verifying connection" in title_lower:
        return False
    if "attention required" in title_lower:
        return False
    if "security verification" == content_stripped.lower():
        return False
    if len(content_stripped) < 150:
        return False
    return True


def _is_relevant_content(
    content: str, title: str, name: str, company: str, source_query: str
) -> bool:
    """Gate 2: verify extracted content is actually about the target entity.

    After Jina extraction we have the FULL page text — much more reliable
    than a 2-sentence snippet. This catches pages that slipped through
    Gate 1 (pre-extraction filter) due to loose matching:
    - Pages about a different person with the same name
    - Pages about a different company with similar generic words
    - Garbage pages that passed _is_useful_content but have no real info

    For company queries: content must mention the company name
    For person queries: content must mention the company name
    (person name check is less critical here because Gate 1 already
    requires both name AND company for person_background results)
    """
    text = f"{(title or '').lower()} {(content or '').lower()}"

    company_lower = company.lower()
    core_parts = [
        p for p in company_lower.split()
        if p not in COMPANY_SUFFIXES and len(p) > 2
    ]
    core_name = " ".join(core_parts)

    if core_name and core_name in text:
        return True

    distinctive = [p for p in core_parts if p not in GENERIC_BUSINESS_WORDS]
    if distinctive and any(p in text for p in distinctive):
        return True

    if source_query == "person_background":
        if _is_name_match(name.lower(), text):
            return True

    return False


async def extract_lead_content(
    serper_results: dict, name: str, company: str
) -> dict:
    """Full pipeline: pre-filter Serper results → extract top N via Jina Reader.

    Extractions run concurrently for speed. URL selection enforces:
    1. Source diversity — at least 1 URL from each Serper query source
    2. Domain diversity — max 1 URL per domain (7 slots = 7 unique sources)
    3. Low-value domains already filtered out in filter_relevant_results
    """
    settings = get_settings()
    if not settings.jina_api_key:
        raise ValueError("JINA_API_KEY not set in environment.")

    max_extractions = settings.max_jina_extractions

    # Count total URLs across all queries
    all_urls = []
    for query_data in serper_results.values():
        for result in query_data.get("results", []):
            if result.get("link"):
                all_urls.append(result["link"])

    # Pre-filter for relevance (also drops low-value domains)
    relevant = filter_relevant_results(serper_results, name, company)

    # Track what got filtered out (for debugging)
    relevant_urls = {r["url"] for r in relevant}
    filtered_out = [u for u in all_urls if u not in relevant_urls]

    # Phase 1: Diversity guarantee — 1 URL from each query source, 1 per domain
    urls_to_extract = []
    seen_urls = set()
    seen_domains = set()
    for source_key in serper_results.keys():
        for r in relevant:
            if r["source_query"] == source_key and r["url"] not in seen_urls:
                domain = urlparse(r["url"]).netloc.lower()
                if domain not in seen_domains:
                    urls_to_extract.append(r)
                    seen_urls.add(r["url"])
                    seen_domains.add(domain)
                    break

    # Phase 2: Fill remaining slots — 1 per domain, relevance order
    for r in relevant:
        if len(urls_to_extract) >= max_extractions:
            break
        domain = urlparse(r["url"]).netloc.lower()
        if r["url"] not in seen_urls and domain not in seen_domains:
            urls_to_extract.append(r)
            seen_urls.add(r["url"])
            seen_domains.add(domain)

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            extract_url(item["url"], settings.jina_api_key, client)
            for item in urls_to_extract
        ]
        results = await asyncio.gather(*tasks)

    # Attach source metadata, filter junk and irrelevant content, count failures
    extractions = []
    failed = 0
    for i, result in enumerate(results):
        source_query = urls_to_extract[i].get("source_query", "")
        if result["error"]:
            failed += 1
            extractions.append(result)
        elif not _is_useful_content(result.get("title", ""), result.get("content", "")):
            failed += 1
            title_l = (result.get("title") or "").lower()
            content_s = (result.get("content") or "").strip()
            if "sign up | linkedin" in title_l or "join linkedin" in title_l:
                reason = f"LinkedIn auth wall (title={title_l[:60]})"
            elif "just a moment" in title_l or "verifying connection" in title_l or "attention required" in title_l:
                reason = f"Cloudflare/bot check (title={title_l[:60]})"
            elif "security verification" == content_s.lower():
                reason = "Security verification page"
            elif len(content_s) < 150:
                reason = f"Content too short ({len(content_s)} chars)"
            else:
                reason = "Unknown filter"
            result["error"] = reason
            extractions.append(result)
        elif not _is_relevant_content(
            result.get("content", ""), result.get("title", ""),
            name, company, source_query,
        ):
            failed += 1
            result["error"] = "Content not about target company/person"
            extractions.append(result)
        else:
            result["source_query"] = source_query
            result["snippet"] = urls_to_extract[i]["snippet"]
            extractions.append(result)

    return {
        "total_urls_found": len(all_urls),
        "relevant_urls": len(relevant),
        "extracted": len(extractions) - failed,
        "failed": failed,
        "filtered_out": filtered_out,
        "extractions": extractions,
        "relevant_results": relevant,
    }
