import asyncio
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
        raise HTTPException(status_code=402, detail=f"Payment not completed. Status: {pi.get('status')}")

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

        # TODO (Task #47): Send email with Excel attachment
        # For now, log that it's ready
        logger.info(
            f"Pipeline complete for {email}: {result.processed} leads processed, "
            f"{result.failed} failed, {result.duration_s}s. Excel ready ({len(excel_bytes)} bytes). "
            f"Email delivery pending."
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

    # 2. Parse the uploaded file
    try:
        leads, skipped = await parse_upload(file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing failed: {str(e)}")

    if not leads:
        raise HTTPException(status_code=400, detail="No valid leads found in file.")

    # 3. Start pipeline in background
    config = PipelineConfig(
        search=True, extract=True, synthesize=True,
        linkedin=False, apollo=False,
    )

    asyncio.create_task(
        _run_and_email(leads, config, email, name, paid_leads)
    )

    return {
        "status": "processing",
        "leads_to_process": min(len(leads), paid_leads),
        "skipped": len(skipped),
        "email": email,
    }
