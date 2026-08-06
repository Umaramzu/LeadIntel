import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.api.upload import router as upload_router
from app.api.debug import router as debug_router
from app.api.process import router as process_router
from app.api.jobs import router as jobs_router
from app.api.landing import router as landing_router
from app.api.stripe_webhook import router as stripe_webhook_router
from app.api.worker import router as worker_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://leadintel.amzuconsulting.ca",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(process_router)
app.include_router(jobs_router)
app.include_router(landing_router)
app.include_router(stripe_webhook_router)
app.include_router(worker_router)
app.include_router(upload_router)
if settings.debug:
    app.include_router(debug_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
    }
