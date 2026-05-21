from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.api.upload import router as upload_router
from app.api.debug import router as debug_router
from app.api.process import router as process_router
from app.api.jobs import router as jobs_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(process_router)
app.include_router(jobs_router)
app.include_router(upload_router)
app.include_router(debug_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
    }
