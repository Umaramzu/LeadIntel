import asyncio
import base64
import logging
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.models import Lead, PipelineConfig
from app.utils.csv_parser import parse_upload
from app.services.pipeline import run_pipeline
from app.services.excel_export import export_pipeline_results
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/landing", tags=["landing"])


async def _verify_payment(payment_intent_id: str) -> dict:
    """Verify a Stripe PaymentIntent is actually paid."""
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured on backend")

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.stripe.com/v1/payment_intents/{payment_intent_id}",
            headers={"Authorization": f"Bearer {settings.stripe_secret_key}"},
        )

    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Invalid payment intent")

    pi = res.json()
    if pi.get("status") != "succeeded":
        raise HTTPException(
            status_code=402, detail=f"Payment not completed. Status: {pi.get('status')}"
        )

    return pi


async def _run_and_email(
    leads: list[Lead],
    config: PipelineConfig,
    email: str,
    customer_name: str,
    max_leads: int,
):
    """Background task: run pipeline on leads, generate Excel, email to customer."""
    try:
        # Cap leads to what they paid for
        leads_to_process = leads[:max_leads]

        result = await run_pipeline(leads_to_process, config)

        # Generate Excel
        buffer = export_pipeline_results(result)
        excel_bytes = buffer.read()

        # Send email with Excel attachment via Resend
        settings = get_settings()
        if not settings.resend_api_key:
            logger.error(
                f"Resend API key not configured. Cannot email results to {email}."
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
                    "from": "LeadIntel <onboarding@resend.dev>",
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
            logger.info(
                f"Email sent to {email}: {result.processed} leads, {len(excel_bytes)} bytes"
            )
        else:
            logger.error(
                f"Email send failed for {email}: {res.status_code} — {res.text}"
            )

    except Exception as e:
        logger.exception(f"Background pipeline failed for {email}: {e}")


@router.post("/process")
async def landing_process(
    file: UploadFile = File(...),
    payment_intent_id: str = Form(...),
    email: str = Form(...),
    name: str = Form(...),
    leads_count: int = Form(...),
):
    """Landing page endpoint: verify payment → parse CSV → run pipeline → email results.
    No auth required — payment intent serves as authorization."""

    # 1. Verify the payment is real and succeeded
    pi = await _verify_payment(payment_intent_id)
    paid_leads = int(pi.get("metadata", {}).get("leads", 0))

    if paid_leads != leads_count:
        raise HTTPException(
            status_code=400,
            detail=f"Lead count mismatch: paid for {paid_leads}, requested {leads_count}",
        )

    # 2. Parse the uploaded file (no per-run cap — we cap by payment tier)
    try:
        leads, skipped = await parse_upload(file, enforce_cap=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing failed: {str(e)}")

    if not leads:
        raise HTTPException(status_code=400, detail="No valid leads found in file.")

    if len(leads) + len(skipped) > 500:
        raise HTTPException(
            status_code=400,
            detail=f"File has {len(leads) + len(skipped)} rows — maximum is 500 per file. Please split into smaller batches.",
        )

    # 3. Start pipeline in background (cap to paid_leads count)
    config = PipelineConfig(
        search=True,
        extract=True,
        synthesize=True,
        linkedin=True,
        apollo=False,
    )

    actual_to_process = min(len(leads), paid_leads)

    asyncio.create_task(_run_and_email(leads, config, email, name, paid_leads))

    return {
        "status": "processing",
        "total_in_file": len(leads) + len(skipped),
        "valid_leads": len(leads),
        "leads_to_process": actual_to_process,
        "skipped": len(skipped),
        "email": email,
    }
