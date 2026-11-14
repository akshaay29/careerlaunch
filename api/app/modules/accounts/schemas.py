"""accounts.schemas: API DTOs."""

import uuid
from typing import Literal

from pydantic import BaseModel


class Me(BaseModel):
    """The calling account. ``role`` comes from the database, never from the token."""

    id: uuid.UUID
    email: str
    role: Literal["student", "admin"]
