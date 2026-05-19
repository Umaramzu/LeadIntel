import httpx
import asyncio
from app.config import get_settings

PROFILE_ACTOR_ID = "dev_fusion~linkedin-profile-scraper"
POSTS_ACTOR_ID = "apimaestro~linkedin-batch-profile-posts-scraper"

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 3
MAX_POLL_SECONDS = 120

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
        while status not in TERMINAL_STATUSES and elapsed < MAX_POLL_SECONDS:
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
    for i in range(4):
        prefix = f"experiences/{i}"
        title = raw.get(f"{prefix}/title")
        company = raw.get(f"{prefix}/companyName")
        if title and company:
            experiences.append({
                "title": title,
                "company": company,
                "industry": raw.get(f"{prefix}/companyIndustry", ""),
                "started": raw.get(f"{prefix}/jobStartedOn", ""),
                "ended": raw.get(f"{prefix}/jobEndedOn", ""),
                "still_working": raw.get(f"{prefix}/jobStillWorking", False),
                "location": raw.get(f"{prefix}/jobLocation", ""),
            })

    skills = []
    for i in range(20):
        s = raw.get(f"skills/{i}/title")
        if s:
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
        "is_creator": raw.get("isCreator", False),
        "is_premium": raw.get("isPremium", False),
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
        posts.append({
            "text": text[:1000],
            "date": raw.get("posted_at/date", ""),
            "relative_date": raw.get("posted_at/relative", ""),
            "post_type": raw.get("post_type", ""),
            "media_type": raw.get("media/type", ""),
            "likes": _safe_int(raw.get("stats/like")),
            "comments": _safe_int(raw.get("stats/comments")),
            "reposts": _safe_int(raw.get("stats/reposts")),
            "total_reactions": _safe_int(raw.get("stats/total_reactions")),
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
    raw_items = await _run_actor(POSTS_ACTOR_ID, {
        "profileUrls": [linkedin_url],
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
