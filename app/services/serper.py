import re
import httpx
from urllib.parse import urlparse
from app.config import get_settings

SERPER_URL = "https://google.serper.dev/search"

# Compound TLDs must precede their simple versions (co.uk before co)
DOMAIN_TLD_RE = (
    r'(?:co\.uk|com\.au|co\.nz|co\.in|com\.br|co\.za|com\.sg|co\.jp'
    r'|com|io|co|org|net|ai|dev|app|me|us|uk|de|fr|ca|in|au|nz|ie|ch'
    r'|se|no|dk|nl|be|it|es|pt|jp|kr|sg|za|ae|mx|br)'
)


def _company_to_slug(company_lower: str) -> str:
    """Company name to domain-comparable slug: strip apostrophes, &, spaces."""
    return (company_lower
            .replace("’", "").replace("'", "")
            .replace("&", "")
            .replace(" ", ""))


def _company_leading_slugs(company_lower: str) -> set:
    """Progressive leading-word concatenations of a company name, so a domain
    that truncates the full legal name still matches the company:
    'valoris group limited'    -> {'valoris', 'valorisgroup', 'valorisgrouplimited'}
    'babsco supply'            -> {'babsco', 'babscosupply'}
    'the psychiatry group pllc'-> {'psychiatry', 'psychiatrygroup', ...}

    Leading articles ('the'/'a'/'an') are dropped so 'The Psychiatry Group'
    still resolves to psychiatrygroup.com.
    """
    words = [w for w in re.sub(r"[^\w\s]", " ", company_lower).split() if w]
    while words and words[0] in {"the", "a", "an"}:
        words.pop(0)
    slugs, acc = set(), ""
    for w in words:
        acc += w
        if len(acc) >= 4:
            slugs.add(acc)
    return slugs


def _domain_is_company(url_domain: str, company_lower: str, company_slug: str) -> bool:
    """True if a result's domain plausibly IS the company's own website.

    Matches the exact slug, domains that extend it (honeycomb -> honeycomb.io),
    and — the case the old startswith check missed — domains that TRUNCATE the
    full legal name by dropping trailing words like 'group'/'limited'/'supply'
    (valorisgroup.co.uk for 'Valoris Group Limited', babsco.com for 'Babsco Supply').
    """
    first_label = url_domain.split(".")[0]
    if first_label.startswith(company_slug):
        return True
    return first_label in _company_leading_slugs(company_lower)


THIRD_PARTY_DOMAINS = {
    "linkedin.com", "youtube.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com", "pinterest.com",
    "businesswire.com", "prnewswire.com", "globenewswire.com",
    "forbes.com", "bloomberg.com", "reuters.com", "yahoo.com",
    "crunchbase.com", "tracxn.com", "pitchbook.com", "cbinsights.com",
    "glassdoor.com", "indeed.com", "comparably.com",
    "trustpilot.com", "g2.com", "gartner.com", "capterra.com",
    "yelp.com", "bbb.org",
    "reddit.com", "medium.com", "substack.com", "wikipedia.org",
    "techcrunch.com", "thenewstack.io", "wired.com", "theverge.com",
    "bcorporation.net", "fintechfutures.com", "reinsurancene.ws",
    "shopperapproved.com", "makeheadway.com",
    "software-engineering-unlocked.com",
    "find-and-update.company-information.service.gov.uk",
}


def _build_queries(name: str, company: str) -> list[dict]:
    return [
        {"label": "company_services", "q": f"{company} services about"},
        {"label": "company_news", "q": f"{company} recent news funding"},
        {"label": "person_background", "q": f"{name} {company} role background"},
        {"label": "company_reviews", "q": f"{company} reviews complaints ratings"},
    ]


def _extract_target_domain(results: dict, company: str) -> str | None:
    """Identify the target company website domain from Phase 1 Serper results.

    Uses two signals ranked by reliability:
    1. person_background -- requires both name AND company match, so URLs and
       domain mentions here are almost certainly the correct entity
    2. company_services -- Google first result for "Company services about"
       is typically the company own website

    Scores each candidate domain and returns the highest.
    Returns None if no candidate found (entity filtering will be skipped).
    """
    company_lower = company.lower()
    if len(company_lower) < 3:
        return None

    company_slug = _company_to_slug(company_lower)
    if len(company_slug) < 3:
        return None

    candidates = {}
    domain_re = re.compile(
        r'\b(' + re.escape(company_slug) + r'[a-z-]*\.' + DOMAIN_TLD_RE + r')\b'
    )

    for r in results.get("person_background", {}).get("results", []):
        url_domain = urlparse(r.get("link", "")).netloc.lower().replace("www.", "")
        if _domain_is_company(url_domain, company_lower, company_slug):
            if not any(tp in url_domain for tp in THIRD_PARTY_DOMAINS):
                candidates[url_domain] = candidates.get(url_domain, 0) + 5

        text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
        for match in domain_re.findall(text):
            candidates[match] = candidates.get(match, 0) + 4

    for i, r in enumerate(results.get("company_services", {}).get("results", [])):
        url_domain = urlparse(r.get("link", "")).netloc.lower().replace("www.", "")
        if _domain_is_company(url_domain, company_lower, company_slug):
            if not any(tp in url_domain for tp in THIRD_PARTY_DOMAINS):
                candidates[url_domain] = candidates.get(url_domain, 0) + (3 if i == 0 else 1)

        text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
        for match in domain_re.findall(text):
            candidates[match] = candidates.get(match, 0) + (2 if i == 0 else 1)

    if not candidates:
        return None

    return max(candidates, key=candidates.get)


async def _run_query(
    client: httpx.AsyncClient, label: str, query: str, headers: dict, num: int
) -> tuple[str, dict]:
    payload = {"q": query, "num": num}
    try:
        resp = await client.post(SERPER_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        organic = data.get("organic", [])
        return label, {
            "query": query,
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
        return label, {
            "query": query,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            "results": [],
        }
    except httpx.RequestError as e:
        return label, {
            "query": query,
            "error": str(e),
            "results": [],
        }


async def search_lead(name: str, company: str) -> dict:
    """Two-phase Serper search with entity disambiguation.

    Phase 1: Run services, news, and person queries to identify the target
    company domain (e.g., honeycomb.io vs honeycombinsurance.com).

    Phase 2: Run reviews query with the target domain included, biasing
    Google toward reviews of the correct entity.
    """
    settings = get_settings()
    if not settings.serper_api_key:
        raise ValueError("SERPER_API_KEY not set in environment.")

    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json",
    }
    num = settings.serper_results_per_query

    results = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        phase1 = [
            ("company_services", f"{company} services about"),
            ("company_news", f"{company} recent news funding"),
            ("person_background", f"{name} {company} role background"),
        ]
        for label, q in phase1:
            lbl, data = await _run_query(client, label, q, headers, num)
            results[lbl] = data

        target_domain = _extract_target_domain(results, company)

        review_q = f"{company} reviews complaints ratings"
        if target_domain:
            review_q = f"{company} {target_domain} reviews complaints ratings"

        lbl, data = await _run_query(client, "company_reviews", review_q, headers, num)
        results[lbl] = data

    results["_meta"] = {"target_domain": target_domain}
    return results
