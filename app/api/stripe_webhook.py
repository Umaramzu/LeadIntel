import hashlib
import hmac
import json
import logging
import time
from fastapi import APIRouter, Request, HTTPException
from app.config import get_settings
from app.services.db import create_order

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stripe", tags=["stripe"])

SIGNATURE_TOLERANCE_SECONDS = 300


def _verify_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe's webhook signature (HMAC-SHA256 over "{timestamp}.{payload}").

    Header format: "t=<timestamp>,v1=<sig>[,v1=<sig>...]" — multiple v1 entries
    appear while a signing secret is being rolled, so all must be checked.
    """
    pairs = [part.split("=", 1) for part in sig_header.split(",") if "=" in part]
    timestamp = next((v for k, v in pairs if k == "t"), None)
    signatures = [v for k, v in pairs if k == "v1"]
    if not timestamp or not signatures:
        return False

    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Records every succeeded payment as an order, independent of the browser.

    If the customer pays but their browser dies before the file upload reaches
    /api/landing/process, this leaves an order in status 'paid' — visible in
    the orders table with the customer's email, so the payment is never lost.
    The landing endpoint later claims 'paid' orders instead of inserting."""
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    if not _verify_signature(payload, sig_header, settings.stripe_webhook_secret):
        logger.warning("Stripe webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = json.loads(payload)
    event_type = event.get("type", "")

    if event_type == "payment_intent.succeeded":
        pi = event.get("data", {}).get("object", {})
        metadata = pi.get("metadata", {})
        order = create_order(
            payment_intent_id=pi["id"],
            email=metadata.get("customer_email") or pi.get("receipt_email") or "unknown",
            customer_name=metadata.get("customer_name"),
            leads_paid=int(metadata.get("leads", 0)),
            amount_cents=pi.get("amount_received") or 0,
            status="paid",
        )
        if order:
            logger.info(
                f"Webhook: payment {pi['id']} recorded as order {order['id']} — awaiting file upload"
            )
        else:
            logger.info(f"Webhook: payment {pi['id']} already has an order — no action")
    else:
        logger.info(f"Webhook: ignoring event type {event_type}")

    return {"received": True}
