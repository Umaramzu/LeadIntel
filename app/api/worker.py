import base64
import logging
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Request, HTTPException
from app.models import Lead, PipelineConfig
from app.services.pipeline import run_pipeline
from app.services.excel_export import export_pipeline_results
from app.services.db import (
    get_order,
    update_order,
    upload_report,
    download_report,
    create_job,
    insert_leads,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/internal", tags=["internal"])

STALE_PROCESSING_MINUTES = 10


async def _send_report_email(
    email: str, customer_name: str, excel_bytes: bytes, processed: int
) -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.resend_api_key:
        return False, "RESEND_API_KEY not configured"

    first_name = customer_name.split()[0] if customer_name else "there"

    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "LeadIntel <noreply@leadintel.amzuconsulting.ca>",
                "to": [email],
                "subject": f"Your LeadIntel Research Report — {processed} Leads",
                "html": (
                    f"<p>Hi {first_name},</p>"
                    f"<p>Your lead research is complete! We processed <strong>{processed} leads</strong>.</p>"
                    f"<p>Your Excel report is attached below. It includes company overviews, "
                    f"key insights, and actionable intelligence for each lead.</p>"
                    f"<p>Thanks for using LeadIntel!</p>"
                    f"<p>— The LeadIntel Team</p>"
                ),
                "attachments": [
                    {
                        "filename": "LeadIntel-Report.xlsx",
                        "content": base64.b64encode(excel_bytes).decode(),
                    }
                ],
            },
        )

    if res.status_code in (200, 201):
        return True, None
    return False, f"Resend HTTP {res.status_code}: {res.text[:300]}"


async def run_order(payload: dict) -> str:
    """Execute an order end-to-end: pipeline → store Excel → email → track status.
    Returns the final order status. Never raises — failures land in the order row."""
    order_id = payload["order_id"]
    email = payload["email"]
    customer_name = payload.get("customer_name") or ""
    file_name = payload.get("file_name") or "upload.csv"

    update_order(order_id, status="processing")
    leads = [Lead(**l) for l in payload["leads"]]
    config = PipelineConfig(
        search=True, extract=True, synthesize=True, linkedin=True, apollo=False
    )

    try:
        job_id = None
        db_lead_ids = None
        try:
            job = create_job(filename=file_name, total_leads=len(leads), config=config)
            job_id = job["id"]
            lead_rows = insert_leads(job_id, leads)
            db_lead_ids = [r["id"] for r in lead_rows]
            update_order(order_id, job_id=job_id)
        except Exception as e:
            logger.warning(
                f"Order {order_id}: job tracking unavailable, running untracked: {e}"
            )

        def heartbeat(processed: int, failed: int, total: int):
            # Bumps updated_at so a retry can tell a live run from a dead one
            try:
                update_order(order_id, leads_done=processed + failed)
            except Exception:
                pass

        result = await run_pipeline(
            leads, config, on_progress=heartbeat, job_id=job_id, db_lead_ids=db_lead_ids
        )

        buffer = export_pipeline_results(result)
        excel_bytes = buffer.read()

        # Store the report before emailing — an email failure must not lose it
        try:
            excel_path = upload_report(order_id, excel_bytes)
            update_order(order_id, excel_path=excel_path)
        except Exception as e:
            logger.error(f"Order {order_id}: report upload to storage failed: {e}")

        ok, err = await _send_report_email(
            email, customer_name, excel_bytes, result.processed
        )
        if ok:
            update_order(order_id, status="completed")
            logger.info(
                f"Order {order_id} completed: emailed {email} — "
                f"{result.processed} leads ok, {result.failed} failed"
            )
            return "completed"

        update_order(order_id, status="email_failed", error=err)
        logger.error(f"Order {order_id}: email send failed for {email}: {err}")
        return "email_failed"

    except Exception as e:
        logger.exception(f"Order {order_id}: pipeline failed: {e}")
        try:
            update_order(
                order_id,
                status="pipeline_failed",
                error=f"{type(e).__name__}: {str(e)[:400]}",
            )
        except Exception:
            logger.exception(f"Order {order_id}: could not record failure status")
        return "pipeline_failed"


async def _retry_email_only(order: dict, payload: dict) -> str:
    """Pipeline already succeeded, only the email failed — resend from storage."""
    order_id = order["id"]
    excel_bytes = download_report(order["excel_path"])
    processed = order.get("leads_done") or order.get("valid_leads") or 0
    ok, err = await _send_report_email(
        payload["email"], payload.get("customer_name") or "", excel_bytes, processed
    )
    if ok:
        update_order(order_id, status="completed")
        logger.info(f"Order {order_id}: email retry succeeded")
        return "completed"
    update_order(order_id, status="email_failed", error=err)
    logger.error(f"Order {order_id}: email retry failed: {err}")
    return "email_failed"


@router.post("/process-order")
async def process_order(request: Request):
    """Cloud Tasks worker. Runs the pipeline inside a request so Cloud Run never
    reclaims the instance mid-run (background tasks have no such guarantee).

    Idempotent under Cloud Tasks retries:
    - completed order            -> ack, nothing to do
    - live run (fresh heartbeat) -> ack, let the original finish
    - dead run (stale heartbeat) -> re-run from scratch
    - email_failed with stored   -> resend email only, never re-run the pipeline
    """
    settings = get_settings()
    if (
        not settings.task_auth_token
        or request.headers.get("X-Task-Auth") != settings.task_auth_token
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = await request.json()
    order_id = payload.get("order_id")
    order = get_order(order_id) if order_id else None
    if not order:
        logger.error(f"Worker: order {order_id} not found — dropping task")
        return {"status": "dropped"}

    status = order["status"]

    if status == "completed":
        return {"status": "already_completed"}

    if status == "processing":
        stale = True
        try:
            updated_at = datetime.fromisoformat(order["updated_at"])
            age_min = (datetime.now(timezone.utc) - updated_at).total_seconds() / 60
            stale = age_min >= STALE_PROCESSING_MINUTES
        except (ValueError, TypeError):
            logger.warning(f"Worker: order {order_id} unparseable updated_at — treating as stale")
        if not stale:
            logger.info(f"Worker: order {order_id} has a live run — ack")
            return {"status": "in_progress"}
        logger.warning(f"Worker: order {order_id} heartbeat stale — assuming dead, re-running")

    if status == "email_failed" and order.get("excel_path"):
        final = await _retry_email_only(order, payload)
    else:
        final = await run_order(payload)

    if final != "completed":
        # Non-2xx makes Cloud Tasks retry with backoff (max 5 attempts)
        raise HTTPException(status_code=500, detail=f"Order ended in {final}")
    return {"status": "completed"}
