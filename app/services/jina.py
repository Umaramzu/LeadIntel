import asyncio
import httpx
from app.config import get_settings

JINA_READER_URL = "https://r.jina.ai"
MAX_CONTENT_LENGTH = 5000  # chars per URL — keeps token usage reasonable


def filter_relevant_results(
    serper_results: dict, name: str, company: str
) -> list[dict]:
    """Pre-filter: only keep search results where the snippet or title
    mentions the company or person name. Drops obvious noise before
    we spend Jina tokens extracting content."""
    name_lower = name.lower()
    company_lower = company.lower()

    # Also check individual name parts (first/last) and company words
    name_parts = [p for p in name_lower.split() if len(p) > 2]
    company_parts = [p for p in company_lower.split() if len(p) > 2]

    relevant = []
    seen_urls = set()

    for query_key, query_data in serper_results.items():
        for result in query_data.get("results", []):
            url = result.get("link", "")
            if not url or url in seen_urls:
                continue

            title = result.get("title", "").lower()
            snippet = result.get("snippet", "").lower()
            text = f"{title} {snippet}"

            # Check if company or person name (or parts) appear in the result
            company_match = company_lower in text or any(
                p in text for p in company_parts
            )
            name_match = name_lower in text or any(
                p in text for p in name_parts
            )

            if company_match or name_match:
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

        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH] + "\n\n[...truncated]"

        return {
            "url": url,
            "title": parsed["title"],
            "content": content,
            "description": parsed["description"],
            "tokens": parsed["tokens"],
            "error": None,
        }
    except httpx.HTTPStatusError as e:
        return {
            "url": url,
            "title": "",
            "content": "",
            "description": "",
            "tokens": 0,
            "error": f"HTTP {e.response.status_code}",
        }
    except (httpx.RequestError, Exception) as e:
        return {
            "url": url,
            "title": "",
            "content": "",
            "description": "",
            "tokens": 0,
            "error": str(e)[:200],
        }


async def extract_lead_content(
    serper_results: dict, name: str, company: str, max_extractions: int = 5
) -> dict:
    """Full pipeline: pre-filter Serper results → extract top N via Jina Reader.

    Extractions run concurrently for speed (vs sequential ~58s → ~12s).
    """
    settings = get_settings()
    if not settings.jina_api_key:
        raise ValueError("JINA_API_KEY not set in environment.")

    # Count total URLs across all queries
    all_urls = []
    for query_data in serper_results.values():
        for result in query_data.get("results", []):
            if result.get("link"):
                all_urls.append(result["link"])

    # Pre-filter for relevance
    relevant = filter_relevant_results(serper_results, name, company)

    # Track what got filtered out (for debugging)
    relevant_urls = {r["url"] for r in relevant}
    filtered_out = [u for u in all_urls if u not in relevant_urls]

    # Extract top N relevant URLs — concurrently
    urls_to_extract = relevant[:max_extractions]

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            extract_url(item["url"], settings.jina_api_key, client)
            for item in urls_to_extract
        ]
        results = await asyncio.gather(*tasks)

    # Attach source metadata and count failures
    extractions = []
    failed = 0
    for i, result in enumerate(results):
        if result["error"]:
            failed += 1
        else:
            result["source_query"] = urls_to_extract[i]["source_query"]
            result["snippet"] = urls_to_extract[i]["snippet"]
        extractions.append(result)

    return {
        "total_urls_found": len(all_urls),
        "relevant_urls": len(relevant),
        "extracted": len(extractions) - failed,
        "failed": failed,
        "filtered_out": filtered_out,
        "extractions": extractions,
    }
