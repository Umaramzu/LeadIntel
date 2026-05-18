from pydantic import BaseModel, Field
from enum import Enum


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Lead(BaseModel):
    name: str
    company: str
    email: str | None = None
    linkedin: str | None = None
    extra: dict = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    apollo: bool = False
    search: bool = True
    extract: bool = True
    synthesize: bool = True


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    total_leads: int = 0
    processed: int = 0
    failed: int = 0
    config: PipelineConfig = Field(default_factory=PipelineConfig)
