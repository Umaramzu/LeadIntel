from datetime import datetime, timedelta, timezone
from postgrest.exceptions import APIError
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


# ── Deduplication ──

def find_existing_research(linkedin_urls: list[str], max_age_days: int | None = None) -> dict[str, dict]:
    """Look up already-processed leads by LinkedIn URL.
    Only returns results newer than max_age_days (default from settings).
    Returns {linkedin_url: {lead row + research_results}} for leads that have fresh completed research."""
    if not linkedin_urls:
        return {}

    if max_age_days is None:
        max_age_days = get_settings().cache_max_age_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    result = (
        get_db()
        .table("leads")
        .select("linkedin_url, name, company, email, status, created_at, research_results(*)")
        .in_("linkedin_url", linkedin_urls)
        .eq("status", "completed")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .execute()
    )
    existing = {}
    for row in result.data:
        url = row["linkedin_url"]
        if url in existing:
            continue
        rr = row.get("research_results")
        if rr:
            existing[url] = row
    return existing


# ── Orders (landing page) ──

def create_order(
    payment_intent_id: str,
    email: str,
    customer_name: str | None,
    leads_paid: int,
    amount_cents: int,
    status: str = "processing",
) -> dict | None:
    """Insert an order. Returns None if this payment_intent_id already has one."""
    try:
        result = get_db().table("orders").insert({
            "payment_intent_id": payment_intent_id,
            "email": email,
            "customer_name": customer_name,
            "leads_paid": leads_paid,
            "amount_cents": amount_cents,
            "status": status,
        }).execute()
        return result.data[0]
    except APIError as e:
        if e.code == "23505":
            return None
        raise


def claim_order(payment_intent_id: str) -> dict | None:
    """Atomically take over an existing order for processing.

    Only orders in 'paid' (webhook recorded payment, file never arrived) or
    'pipeline_failed' (customer retry after our failure) may be claimed.
    Any other status means the payment was already consumed — returns None
    so the caller rejects the request as a replay.
    """
    result = (
        get_db()
        .table("orders")
        .update({"status": "processing", "error": None})
        .eq("payment_intent_id", payment_intent_id)
        .in_("status", ["paid", "pipeline_failed"])
        .execute()
    )
    return result.data[0] if result.data else None


def update_order(order_id: str, **kwargs) -> dict:
    result = get_db().table("orders").update(kwargs).eq("id", order_id).execute()
    return result.data[0]


def get_order_by_payment_intent(payment_intent_id: str) -> dict | None:
    result = (
        get_db()
        .table("orders")
        .select("*")
        .eq("payment_intent_id", payment_intent_id)
        .execute()
    )
    return result.data[0] if result.data else None


def upload_report(order_id: str, excel_bytes: bytes) -> str:
    """Store a generated Excel in the private 'reports' bucket. Returns storage path."""
    path = f"{order_id}.xlsx"
    get_db().storage.from_("reports").upload(
        path,
        excel_bytes,
        file_options={
            "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "upsert": "true",
        },
    )
    return path


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
