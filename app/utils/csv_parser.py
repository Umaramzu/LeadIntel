import io
import pandas as pd
from fastapi import UploadFile, HTTPException
from app.models import Lead

REQUIRED_FIELDS = {"name", "company", "linkedin"}
SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_LEADS_PER_RUN = 100

# Common column name variations → normalized field name
COLUMN_ALIASES = {
    "name": "name",
    "full_name": "name",
    "full name": "name",
    "fullname": "name",
    "contact_name": "name",
    "contact name": "name",
    "first name": "first_name",
    "first_name": "first_name",
    "firstname": "first_name",
    "last name": "last_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "company": "company",
    "company_name": "company",
    "company name": "company",
    "organization": "company",
    "organization name": "company",
    "email": "email",
    "email_address": "email",
    "email address": "email",
    "linkedin": "linkedin",
    "linkedin_url": "linkedin",
    "linkedin url": "linkedin",
    "linkedin_profile": "linkedin",
    "linkedin profile": "linkedin",
    "person linkedin url": "linkedin",
    "person linkedin_url": "linkedin",
    "linkedin profile url": "linkedin",
    "profile url": "linkedin",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = df.columns.str.strip().str.lower()
    rename_map = {}
    for col in df.columns:
        if col in COLUMN_ALIASES:
            rename_map[col] = COLUMN_ALIASES[col]
    df = df.rename(columns=rename_map)

    # Merge first_name + last_name → name (if no "name" column exists)
    if "name" not in df.columns and "first_name" in df.columns:
        if "last_name" in df.columns:
            df["name"] = (df["first_name"].fillna("").astype(str).str.strip()
                          + " "
                          + df["last_name"].fillna("").astype(str).str.strip()).str.strip()
        else:
            df["name"] = df["first_name"]

    return df


def _validate_required_fields(df: pd.DataFrame) -> None:
    missing = REQUIRED_FIELDS - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required columns: {', '.join(sorted(missing))}. File must have 'name', 'company', and 'linkedin' columns.",
        )


def _df_to_leads(df: pd.DataFrame) -> tuple[list[Lead], list[dict]]:
    leads = []
    skipped = []
    known_fields = {"name", "company", "email", "linkedin"}

    for idx, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        company = str(row.get("company", "")).strip()

        if not name or not company or name == "nan" or company == "nan":
            skipped.append({"row": idx + 2, "reason": "Missing name or company"})
            continue

        linkedin = row.get("linkedin")
        linkedin = str(linkedin).strip() if pd.notna(linkedin) and str(linkedin).strip() else None

        if not linkedin:
            skipped.append({"row": idx + 2, "reason": "Missing LinkedIn URL"})
            continue

        extra = {}
        for col in df.columns:
            if col not in known_fields:
                val = row.get(col)
                if pd.notna(val) and str(val).strip():
                    extra[col] = str(val).strip()

        email = row.get("email")
        email = str(email).strip() if pd.notna(email) and str(email).strip() else None

        leads.append(Lead(
            name=name,
            company=company,
            email=email,
            linkedin=linkedin,
            extra=extra,
        ))

    return leads, skipped


async def parse_upload(file: UploadFile, enforce_cap: bool = True) -> tuple[list[Lead], list[dict]]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty.")

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB.")

    if ext == ".csv":
        df = pd.read_csv(io.BytesIO(content))
    else:
        df = pd.read_excel(io.BytesIO(content))

    df = _normalize_columns(df)
    _validate_required_fields(df)

    if enforce_cap and len(df) > MAX_LEADS_PER_RUN:
        raise HTTPException(
            status_code=400,
            detail=f"Too many leads ({len(df)}). Maximum is {MAX_LEADS_PER_RUN} per run. Split your file into smaller batches.",
        )

    return _df_to_leads(df)
