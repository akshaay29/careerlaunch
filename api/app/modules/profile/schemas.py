"""profile.schemas: API DTOs."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.profile.files import ResumeContentType


class ResumeVersion(BaseModel):
    """One uploaded Resume version. ``filename`` is untrusted text: render it as plain text."""

    id: uuid.UUID
    filename: str
    content_type: ResumeContentType
    size_bytes: int
    is_active: bool
    created_at: datetime


class ResumeList(BaseModel):
    """Newest first. Pass ``next_cursor`` as ``cursor`` to get the next page (null = done)."""

    items: list[ResumeVersion]
    next_cursor: uuid.UUID | None


class ResumeDownload(BaseModel):
    """A signed URL that downloads the file as an attachment; it stops working at
    ``expires_at`` (5 minutes or less)."""

    url: str
    expires_at: datetime
