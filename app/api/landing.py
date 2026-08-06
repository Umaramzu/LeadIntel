import asyncio
import logging
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.utils.csv_parser import parse_upload
from app.services.db import create_order, claim_order, update_order
from app.services.tasks import enqueue_order
from app.api.worker import run_order
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

    leads_to_process = leads[:paid_leads]
    update_order(
        order["id"],
        file_name=file.filename,
        valid_leads=len(leads),
        email=email,
        customer_name=name,
    )

    task_payload = {
        "order_id": order["id"],
        "email": email,
        "customer_name": name,
        "file_name": file.filename or "upload.csv",
        "leads": [l.model_dump() for l in leads_to_process],
    }

    try:
        await enqueue_order(task_payload)
        update_order(order["id"], status="queued")
        logger.info(
            f"Order {order['id']} queued: pi={payment_intent_id} "
            f"{len(leads_to_process)} of {len(leads)} valid leads"
        )
    except Exception as e:
        # Enqueue failure must not strand a paid customer — degrade to the
        # old in-process run (works, just without crash recovery)
        logger.error(
            f"Order {order['id']}: enqueue failed ({type(e).__name__}: {e}) — "
            f"falling back to in-process run"
        )
        asyncio.create_task(run_order(task_payload))

    return {
        "status": "processing",
        "order_id": order["id"],
        "total_in_file": len(leads) + len(skipped),
        "valid_leads": len(leads),
        "leads_to_process": len(leads_to_process),
        "skipped": len(skipped),
        "email": email,
    }
