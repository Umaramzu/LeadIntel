import httpx
import asyncio
from app.config import get_settings

PROFILE_ACTOR_ID = "dev_fusion~linkedin-profile-scraper"
POSTS_ACTOR_ID = "apimaestro~linkedin-batch-profile-posts-scraper"

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 3

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


async def _run_actor(actor_id: str, input_data: dict) -> list[dict]:
    """Run an Apify actor synchronously and return dataset items."""
    settings = get_settings()
    if not settings.apify_api_token:
        raise ValueError("APIFY_API_TOKEN not set in environment.")

    headers = {"Authorization": f"Bearer {settings.apify_api_token}"}

    async with httpx.AsyncClient(timeout=180) as client:
        # Start the actor run
        run_resp = await client.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            headers=headers,
            json=input_data,
            params={"waitForFinish": 60},
        )
        run_resp.raise_for_status()
        run_data = run_resp.json()["data"]

        run_id = run_data["id"]
        status = run_data["status"]
        dataset_id = run_data["defaultDatasetId"]

        # Poll if not finished within waitForFinish window
        elapsed = 0
        max_poll = settings.apify_max_poll_seconds
        while status not in TERMINAL_STATUSES and elapsed < max_poll:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            poll_resp = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                headers=headers,
            )
            poll_resp.raise_for_status()
            status = poll_resp.json()["data"]["status"]

        if status != "SUCCEEDED":
            raise RuntimeError(f"Apify actor {actor_id} ended with status: {status}")

        # Fetch dataset items
        items_resp = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=headers,
            params={"format": "json"},
        )
        items_resp.raise_for_status()
        return items_resp.json()


def _extract_profile(raw: dict) -> dict:
    """Extract the fields we care about from a raw profile scraper result."""
    experiences = []
    for exp in (raw.get("experiences") or [])[:4]:
        if not isinstance(exp, dict):
            continue
        title = exp.get("title")
        company = exp.get("companyName")
        if title and company:
            experiences.append({
                "title": title,
                "company": company,
                "industry": exp.get("companyIndustry", ""),
                "started": exp.get("jobStartedOn", ""),
                "ended": exp.get("jobEndedOn", ""),
                "still_working": exp.get("jobStillWorking", False),
                "location": exp.get("jobLocation", ""),
            })

    skills = []
    for s in (raw.get("skills") or [])[:20]:
        if isinstance(s, dict) and s.get("title"):
            skills.append(s["title"])
        elif isinstance(s, str) and s:
            skills.append(s)

    return {
        "full_name": raw.get("fullName", ""),
        "headline": raw.get("headline", ""),
        "about": raw.get("about", ""),
        "job_title": raw.get("jobTitle", ""),
        "company_name": raw.get("companyName", ""),
        "company_industry": raw.get("companyIndustry", ""),
        "company_size": raw.get("companySize", ""),
        "company_website": raw.get("companyWebsite", ""),
        "location": raw.get("addressWithCountry", ""),
        "email": raw.get("email", ""),
        "total_experience_years": raw.get("totalExperienceYears", 0),
        "experiences": experiences,
        "skills": skills,
    }


def _extract_posts(raw_items: list[dict]) -> list[dict]:
    """Extract the fields we care about from raw posts scraper results."""
    posts = []
    for raw in raw_items:
        text = raw.get("text", "")
        if not text:
            continue

        posted_at = raw.get("posted_at") or {}
        stats = raw.get("stats") or {}
        media = raw.get("media") or {}

        posts.append({
            "text": text,
            "date": posted_at.get("date", ""),
            "relative_date": posted_at.get("relative", ""),
            "post_type": raw.get("post_type", ""),
            "media_type": media.get("type", ""),
            "likes": _safe_int(stats.get("like")),
            "comments": _safe_int(stats.get("comments")),
            "reposts": _safe_int(stats.get("reposts")),
            "total_reactions": _safe_int(stats.get("total_reactions")),
            "url": raw.get("url", ""),
        })
    return posts


def _safe_int(val) -> int:
    try:
        return int(val) if val else 0
    except (ValueError, TypeError):
        return 0


async def scrape_linkedin_profile(linkedin_url: str) -> dict:
    """Scrape a single LinkedIn profile. Returns extracted profile dict."""
    raw_items = await _run_actor(PROFILE_ACTOR_ID, {
        "profileUrls": [linkedin_url],
    })
    if not raw_items:
        return {}
    return _extract_profile(raw_items[0])


async def scrape_linkedin_posts(linkedin_url: str) -> list[dict]:
    """Scrape recent posts from a LinkedIn profile. Returns list of post dicts."""
    settings = get_settings()
    raw_items = await _run_actor(POSTS_ACTOR_ID, {
        "usernames": [linkedin_url],
        "limit": settings.apify_posts_limit,
    })
    return _extract_posts(raw_items)


async def scrape_linkedin(linkedin_url: str) -> dict:
    """Run both profile and posts scrapers concurrently for a LinkedIn URL.
    Returns {"profile": {...}, "posts": [...]} or partial results on error."""
    profile_data = {}
    posts_data = []

    # Run both concurrently — one failing shouldn't block the other
    profile_task = asyncio.create_task(scrape_linkedin_profile(linkedin_url))
    posts_task = asyncio.create_task(scrape_linkedin_posts(linkedin_url))

    try:
        profile_data = await profile_task
    except Exception:
        profile_data = {}

    try:
        posts_data = await posts_task
    except Exception:
        posts_data = []

    return {"profile": profile_data, "posts": posts_data}
