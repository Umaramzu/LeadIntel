import base64
import json
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

# Cloud Run's metadata server issues OAuth tokens for the service account —
# no SDK or key file needed (matches this codebase's raw-REST idiom)
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


async def _get_access_token(client: httpx.AsyncClient) -> str:
    res = await client.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    res.raise_for_status()
    return res.json()["access_token"]


async def enqueue_order(payload: dict) -> str:
    """Enqueue an order-processing task on Cloud Tasks. Returns the task name.

    dispatchDeadline is capped by Cloud Tasks at 30 minutes; runs longer than
    that trigger a retry which the worker's idempotency guard acks while the
    original request keeps running (Cloud Run allows up to 60 min per request).
    """
    settings = get_settings()
    if not settings.service_url:
        raise ValueError("SERVICE_URL not configured — cannot enqueue")
    if not settings.task_auth_token:
        raise ValueError("TASK_AUTH_TOKEN not configured — cannot enqueue")

    queue_path = (
        f"projects/{settings.gcp_project}/locations/{settings.tasks_location}"
        f"/queues/{settings.tasks_queue}"
    )
    task = {
        "task": {
            "httpRequest": {
                "url": f"{settings.service_url}/api/internal/process-order",
                "httpMethod": "POST",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Task-Auth": settings.task_auth_token,
                },
                "body": base64.b64encode(json.dumps(payload).encode()).decode(),
            },
            "dispatchDeadline": "1800s",
        }
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        token = await _get_access_token(client)
        res = await client.post(
            f"https://cloudtasks.googleapis.com/v2/{queue_path}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json=task,
        )
        res.raise_for_status()
        name = res.json().get("name", "")
        logger.info(f"Enqueued task {name} for order {payload.get('order_id')}")
        return name
