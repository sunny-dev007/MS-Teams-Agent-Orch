import datetime

from pydantic import BaseModel


class TaskResponse(BaseModel):
    id: str
    status: str
    source: str
    intent: str | None = None
    repo_url: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    error: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class WhatsAppMessage(BaseModel):
    phone: str
    message: str
    message_id: str | None = None


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "0.1.0"
