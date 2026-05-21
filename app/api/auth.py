import logging
from fastapi import Depends, HTTPException, Request
from app.config import get_settings

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> str | None:
    """Extract and verify Supabase JWT from Authorization header.

    Returns user_id (UUID string) on success.
    Returns None if Supabase is not configured (local dev bypass).
    Raises 401 if token is missing/invalid when Supabase IS configured.
    """
    settings = get_settings()

    if not settings.supabase_url or not settings.supabase_service_key:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]

    try:
        from app.services.db import get_db
        response = get_db().auth.get_user(token)
        return response.user.id
    except Exception as e:
        logger.warning(f"Auth verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
