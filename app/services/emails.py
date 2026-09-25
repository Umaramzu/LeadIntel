import html
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)


def _build_confirmation_html(
    first_name: str,
    paid_leads: int,
    amount_usd: str,
    file_name: str,
    valid_leads: int,
    to_process: int,
    delivery_email: str,
    payment_intent_id: str,
) -> str:
    if to_process < valid_leads:
        leads_line = f"{to_process} (first {to_process} of {valid_leads} valid leads in your file)"
    else:
        leads_line = str(to_process)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Order confirmed</title></head>
<body style="margin:0;padding:0;background-color:#fafafa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#fafafa;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr><td style="padding:0 8px 20px 8px;">
          <span style="font-size:20px;font-weight:600;color:#18181b;letter-spacing:-0.02em;">Lead<span style="color:#2563eb;">Intel</span></span>
        </td></tr>
        <tr><td style="height:5px;background-color:#2563eb;border-radius:16px 16px 0 0;font-size:0;line-height:0;">&nbsp;</td></tr>
        <tr><td style="background-color:#ffffff;border:1px solid #e4e4e7;border-top:0;border-radius:0 0 16px 16px;padding:40px 40px 32px 40px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding-bottom:20px;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr>
                <td align="center" valign="middle" style="width:56px;height:56px;border-radius:50%;background-color:#10b981;font-size:28px;line-height:56px;color:#ffffff;font-weight:700;">&#10003;</td>
              </tr></table>
            </td></tr>
            <tr><td align="center" style="padding-bottom:8px;">
              <span style="font-size:24px;font-weight:600;color:#18181b;letter-spacing:-0.02em;">Order confirmed</span>
            </td></tr>
            <tr><td align="center" style="padding-bottom:28px;">
              <span style="font-size:15px;color:#52525b;line-height:1.6;">Hi {first_name}, thanks for your order &mdash; your payment went through and your research is already underway.</span>
            </td></tr>
            <tr><td style="background-color:#fafafa;border:1px solid #e4e4e7;border-radius:12px;padding:20px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;">
                <tr><td style="color:#71717a;padding:6px 0;">Order</td><td align="right" style="color:#18181b;font-weight:500;padding:6px 0;">Lead research &mdash; {paid_leads} leads</td></tr>
                <tr><td style="color:#71717a;padding:6px 0;">Amount paid</td><td align="right" style="color:#18181b;font-weight:600;padding:6px 0;">{amount_usd} USD</td></tr>
                <tr><td style="color:#71717a;padding:6px 0;">File received</td><td align="right" style="color:#18181b;font-weight:500;padding:6px 0;">{file_name}</td></tr>
                <tr><td style="color:#71717a;padding:6px 0;">Leads to research</td><td align="right" style="color:#18181b;font-weight:500;padding:6px 0;">{leads_line}</td></tr>
                <tr><td style="color:#71717a;padding:6px 0;">Delivery inbox</td><td align="right" style="color:#18181b;font-weight:500;padding:6px 0;">{delivery_email}</td></tr>
                <tr><td colspan="2" style="border-top:1px solid #e4e4e7;padding-top:12px;">
                  <span style="color:#71717a;font-size:12px;">Order reference</span><br>
                  <span style="color:#3f3f46;font-size:12px;font-family:Consolas,Menlo,monospace;">{payment_intent_id}</span>
                </td></tr>
              </table>
            </td></tr>
            <tr><td style="padding-top:28px;">
              <span style="font-size:14px;font-weight:600;color:#18181b;">What happens next</span>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;font-size:14px;color:#52525b;line-height:1.7;">
                <tr><td style="padding:2px 0;"><span style="color:#2563eb;font-weight:600;">1.</span> &nbsp;We research every lead &mdash; web search, LinkedIn activity, AI synthesis.</td></tr>
                <tr><td style="padding:2px 0;"><span style="color:#2563eb;font-weight:600;">2.</span> &nbsp;Your Excel research dossier lands in this inbox <span style="color:#18181b;font-weight:500;">within 1 hour</span> (usually minutes).</td></tr>
                <tr><td style="padding:2px 0;"><span style="color:#2563eb;font-weight:600;">3.</span> &nbsp;Open it and start your outreach &mdash; every claim cited to a real source.</td></tr>
              </table>
            </td></tr>
            <tr><td style="padding-top:24px;border-top:1px solid #f4f4f5;">
              <span style="font-size:13px;color:#a1a1aa;line-height:1.6;">If your report doesn't arrive within a few hours, check your spam folder first, then contact us and mention your order reference above.</span>
            </td></tr>
          </table>
        </td></tr>
        <tr><td align="center" style="padding:24px 8px;">
          <span style="font-size:12px;color:#a1a1aa;">LeadIntel by AMZU Consulting &nbsp;&middot;&nbsp; <a href="https://leadintel.amzuconsulting.ca" style="color:#2563eb;text-decoration:none;">leadintel.amzuconsulting.ca</a></span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_confirmation_email(
    email: str,
    customer_name: str,
    paid_leads: int,
    amount_cents: int,
    file_name: str,
    valid_leads: int,
    to_process: int,
    payment_intent_id: str,
):
    """Fire-and-forget order confirmation. Failure is logged, never raised —
    the confirmation must not block or fail an accepted order."""
    settings = get_settings()
    if not settings.resend_api_key:
        logger.error("RESEND_API_KEY not configured — cannot send confirmation email")
        return

    first_name = html.escape(customer_name.split()[0]) if customer_name.strip() else "there"
    body_html = _build_confirmation_html(
        first_name=first_name,
        paid_leads=paid_leads,
        amount_usd=f"${amount_cents / 100:.2f}",
        file_name=html.escape(file_name),
        valid_leads=valid_leads,
        to_process=to_process,
        delivery_email=html.escape(email),
        payment_intent_id=html.escape(payment_intent_id),
    )

    try:
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
                    "subject": f"Order confirmed — Lead research, {paid_leads} leads",
                    "html": body_html,
                },
            )
        if res.status_code in (200, 201):
            logger.info(f"Confirmation email sent to {email} for {payment_intent_id}")
        else:
            logger.error(
                f"Confirmation email failed for {payment_intent_id}: "
                f"HTTP {res.status_code} — {res.text[:300]}"
            )
    except Exception as e:
        logger.error(
            f"Confirmation email failed for {payment_intent_id}: {type(e).__name__}: {e}"
        )
