from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class LinkCreate(BaseModel):
    url: HttpUrl


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    target_url: str
    clicks: int
    created_at: datetime


class LinkStats(BaseModel):
    code: str
    clicks: int
    pending_clicks:int
    recent_clicks: list[datetime]
