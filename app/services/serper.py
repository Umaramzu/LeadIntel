import httpx
from app.config import get_settings

SERPER_URL = "https://google.serper.dev/search"


def _build_queries(name: str, company: str) -> list[dict]:
    return [
        {"label": "company_services", "q": f"{company} services about"},
        {"label": "company_news", "q": f"{company} recent news funding"},
        {"label": "person_background", "q": f"{name} {company} role background"},
        {"label": "company_reviews", "q": f"{company} reviews complaints ratings"},
    ]


async def search_lead(name: str, company: str, num_results: int = 5) -> dict:
    settings = get_settings()
    if not settings.serper_api_key:
        raise ValueError("SERPER_API_KEY not set in environment.")

    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json",
    }

    queries = _build_queries(name, company)
    results = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for query_info in queries:
            payload = {"q": query_info["q"], "num": num_results}
            try:
                resp = await client.post(SERPER_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                organic = data.get("organic", [])
                results[query_info["label"]] = {
                    "query": query_info["q"],
                    "results": [
                        {
                            "title": item.get("title", ""),
                            "link": item.get("link", ""),
                            "snippet": item.get("snippet", ""),
                        }
                        for item in organic
                    ],
                }
            except httpx.HTTPStatusError as e:
                results[query_info["label"]] = {
                    "query": query_info["q"],
                    "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                    "results": [],
                }
            except httpx.RequestError as e:
                results[query_info["label"]] = {
                    "query": query_info["q"],
                    "error": str(e),
                    "results": [],
                }

    return results
