import asyncio
import base64
import logging
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.models import Lead, PipelineConfig
from app.utils.csv_parser import parse_upload
from app.services.pipeline import run_pipeline
from app.services.excel_export import export_pipeline_results
from app.services.db import (
    create_order,
    claim_order,
    update_order,
    upload_report,
    create_job,
    insert_leads,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/landing", tags=["landing"])

# Must stay in sync with landing/src/lib/pricing.ts
TIER_AMOUNT_CENTS = {25: 999, 50: 1799, 100: 2999}


async def _verify_payment(payment_intent_id: str) -> dict:
    """Verify a Stripe PaymentIntent is actually paid."""
    settings = get_settings()
    if not settings.stripe_secret_key:
        logger.error("stripe_secret_key not configured — cannot verify payments")
        raise HTTPException(status_code=500, detail="Stripe not configured on backend")

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.stripe.com/v1/payment_intents/{payment_intent_id}",
            headers={"Authorization": f"Bearer {settings.stripe_secret_key}"},
        )

    if res.status_code != 200:
        logger.warning(
            f"Stripe PI lookup failed: {payment_intent_id} — HTTP {res.status_code}: {res.text[:200]}"
        )
        raise HTTPException(status_code=400, detail="Invalid payment intent")

    pi = res.json()
    if pi.get("status") != "succeeded":
        logger.warning(
            f"Payment not completed: {payment_intent_id} status={pi.get('status')}"
        )
        raise HTTPException(
            status_code=402, detail=f"Payment not completed. Status: {pi.get('status')}"
        )

    return pi


async def _run_and_email(
    leads: list[Lead],
    config: PipelineConfig,
    email: str,
    customer_name: str,
    order_id: str,
    file_name: str,
):
    """Background task: run pipeline, store Excel, email customer, track order status."""
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

        result = await run_pipeline(leads, config, job_id=job_id, db_lead_ids=db_lead_ids)

        buffer = export_pipeline_results(result)
        excel_bytes = buffer.read()

        # Store the report before emailing — an email failure must not lose it
        try:
            excel_path = upload_report(order_id, excel_bytes)
            update_order(order_id, excel_path=excel_path)
        except Exception as e:
            logger.error(f"Order {order_id}: report upload to storage failed: {e}")

        settings = get_settings()
        if not settings.resend_api_key:
            logger.error(
                f"Order {order_id}: RESEND_API_KEY not configured, cannot email {email}"
            )
            update_order(
                order_id, status="email_failed", error="RESEND_API_KEY not configured"
            )
            return

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
                    "subject": f"Your LeadIntel Research Report — {result.processed} Leads",
                    "html": (
                        f"<p>Hi {first_name},</p>"
                        f"<p>Your lead research is complete! We processed <strong>{result.processed} leads</strong>.</p>"
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
            update_order(order_id, status="completed")
            logger.info(
                f"Order {order_id} completed: emailed {email} — "
                f"{result.processed} leads ok, {result.failed} failed, {len(excel_bytes)} bytes"
            )
        else:
            update_order(
                order_id,
                status="email_failed",
                error=f"Resend HTTP {res.status_code}: {res.text[:300]}",
            )
            logger.error(
                f"Order {order_id}: email send failed for {email}: {res.status_code} — {res.text}"
            )

    except Exception as e:
        logger.exception(f"Order {order_id}: background pipeline failed for {email}: {e}")
        try:
            update_order(
                order_id,
                status="pipeline_failed",
                error=f"{type(e).__name__}: {str(e)[:400]}",
            )
        except Exception:
            logger.exception(f"Order {order_id}: could not record failure status")


@router.post("/process")
async def landing_process(
    file: UploadFile = File(...),
    payment_intent_id: str = Form(...),
    email: str = Form(...),
    name: str = Form(...),
    leads_count: int = Form(...),
):
    """Landing page endpoint: verify payment → record order → parse CSV → run pipeline → email results.
    No auth required — a verified, unconsumed payment intent serves as authorization."""
    logger.info(
        f"Landing order request: pi={payment_intent_id} email={email} "
        f"leads_count={leads_count} file={file.filename}"
    )

    pi = await _verify_payment(payment_intent_id)
    paid_leads = int(pi.get("metadata", {}).get("leads", 0))

    if paid_leads != leads_count:
        logger.warning(
            f"pi={payment_intent_id}: lead count mismatch paid={paid_leads} requested={leads_count}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Lead count mismatch: paid for {paid_leads}, requested {leads_count}",
        )

    expected_cents = TIER_AMOUNT_CENTS.get(paid_leads)
    if (
        expected_cents is None
        or pi.get("amount_received") != expected_cents
        or pi.get("currency") != "usd"
    ):
        logger.warning(
            f"pi={payment_intent_id}: amount mismatch — leads={paid_leads} "
            f"amount_received={pi.get('amount_received')} currency={pi.get('currency')} "
            f"expected={expected_cents}"
        )
        raise HTTPException(
            status_code=400, detail="Payment amount does not match the selected tier"
        )

    # Record the order — the UNIQUE payment_intent_id is the replay gate
    order = create_order(
        payment_intent_id=payment_intent_id,
        email=email,
        customer_name=name,
        leads_paid=paid_leads,
        amount_cents=expected_cents,
    )
    if order is None:
        order = claim_order(payment_intent_id)
        if order is None:
            logger.warning(
                f"pi={payment_intent_id}: replay blocked — payment already consumed"
            )
            raise HTTPException(
                status_code=409,
                detail="This payment has already been used to start research. "
                "Contact support if you did not receive your report.",
            )

    try:
        leads, skipped = await parse_upload(file, enforce_cap=False)
    except HTTPException as e:
        update_order(
            order["id"],
            status="pipeline_failed",
            error=f"File parsing failed: {e.detail}",
            file_name=file.filename,
        )
        raise
    except Exception as e:
        update_order(
            order["id"],
            status="pipeline_failed",
            error=f"File parsing failed: {str(e)[:300]}",
            file_name=file.filename,
        )
        raise HTTPException(status_code=400, detail=f"File parsing failed: {str(e)}")

    if not leads:
        update_order(
            order["id"],
            status="pipeline_failed",
            error="No valid leads found in file",
            file_name=file.filename,
        )
        raise HTTPException(status_code=400, detail="No valid leads found in file.")

    if len(leads) + len(skipped) > 500:
        update_order(
            order["id"],
            status="pipeline_failed",
            error=f"File has {len(leads) + len(skipped)} rows (max 500)",
            file_name=file.filename,
        )
        raise HTTPException(
            status_code=400,
            detail=f"File has {len(leads) + len(skipped)} rows — maximum is 500 per file. Please split into smaller batches.",
        )

    config = PipelineConfig(
        search=True,
        extract=True,
        synthesize=True,
        linkedin=True,
        apollo=False,
    )

    leads_to_process = leads[:paid_leads]
    update_order(
        order["id"],
        file_name=file.filename,
        valid_leads=len(leads),
        email=email,
        customer_name=name,
    )

    asyncio.create_task(
        _run_and_email(
            leads_to_process, config, email, name,
            order["id"], file.filename or "upload.csv",
        )
    )

    logger.info(
        f"Order {order['id']} started: pi={payment_intent_id} "
        f"processing {len(leads_to_process)} of {len(leads)} valid leads"
    )

    return {
        "status": "processing",
        "order_id": order["id"],
        "total_in_file": len(leads) + len(skipped),
        "valid_leads": len(leads),
        "leads_to_process": len(leads_to_process),
        "skipped": len(skipped),
        "email": email,
    }
