from supabase import create_client, Client
from app.config import get_settings
from app.models import Lead, PipelineConfig

_client: Client | None = None


def get_db() -> Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_service_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


# ── Jobs ──

def create_job(filename: str, total_leads: int, config: PipelineConfig, user_id: str | None = None) -> dict:
    data = {
        "filename": filename,
        "total_leads": total_leads,
        "status": "processing",
        "config": config.model_dump(),
    }
    if user_id:
        data["user_id"] = user_id
    result = get_db().table("jobs").insert(data).execute()
    return result.data[0]


def update_job(job_id: str, **kwargs) -> dict:
    result = get_db().table("jobs").update(kwargs).eq("id", job_id).execute()
    return result.data[0]


def get_job(job_id: str) -> dict | None:
    result = get_db().table("jobs").select("*").eq("id", job_id).single().execute()
    return result.data


def list_jobs(user_id: str | None = None, limit: int = 50) -> list[dict]:
    query = get_db().table("jobs").select("*").order("created_at", desc=True).limit(limit)
    if user_id:
        query = query.eq("user_id", user_id)
    result = query.execute()
    return result.data


# ── Leads ──

def insert_leads(job_id: str, leads: list[Lead]) -> list[dict]:
    rows = [
        {
            "job_id": job_id,
            "name": lead.name,
            "company": lead.company,
            "email": lead.email,
            "linkedin_url": lead.linkedin,
            "extra": lead.extra,
            "status": "pending",
        }
        for lead in leads
    ]
    result = get_db().table("leads").insert(rows).execute()
    return result.data


def update_lead(lead_id: str, **kwargs) -> dict:
    result = get_db().table("leads").update(kwargs).eq("id", lead_id).execute()
    return result.data[0]


def get_leads_for_job(job_id: str) -> list[dict]:
    result = (
        get_db()
        .table("leads")
        .select("*, research_results(*)")
        .eq("job_id", job_id)
        .order("created_at")
        .execute()
    )
    return result.data


# ── Research Results ──

def insert_research_result(
    lead_id: str,
    research: dict,
    source_urls: list[str],
    linkedin_data: dict | None = None,
) -> dict:
    data = {
        "lead_id": lead_id,
        "company_snapshot": research.get("company_snapshot", {}),
        "prospect_role": research.get("prospect_role", {}),
        "pain_signals": research.get("pain_signals", []),
        "confidence_score": research.get("confidence_score"),
        "data_gaps": research.get("data_gaps", []),
        "source_urls": source_urls,
    }
    if linkedin_data:
        data["linkedin_data"] = linkedin_data
    result = get_db().table("research_results").insert(data).execute()
    return result.data[0]
