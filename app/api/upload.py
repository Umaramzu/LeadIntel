from fastapi import APIRouter, UploadFile, File
from app.utils.csv_parser import parse_upload

router = APIRouter(prefix="/api")


@router.post("/upload")
async def upload_leads(file: UploadFile = File(...)):
    leads, skipped = await parse_upload(file)
    return {
        "total_parsed": len(leads),
        "total_skipped": len(skipped),
        "leads": [lead.model_dump() for lead in leads],
        "skipped": skipped,
    }
