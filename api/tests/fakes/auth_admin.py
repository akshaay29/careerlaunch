"""In-memory AuthAdmin fake (AD-3). Tests never touch real Supabase Auth."""

import uuid
from dataclasses import dataclass, field

from app.ports.auth_admin import AuthAdminError


@dataclass
class FakeAuthAdmin:
    """Auth users by id. Set ``fail_delete`` to make ``delete_user`` raise
    :class:`AuthAdminError`. Deleting a missing user succeeds (like the adapter's 404)."""

    users: set[uuid.UUID] = field(default_factory=set)
    deleted: list[uuid.UUID] = field(default_factory=list)
    fail_delete: bool = False

    async def delete_user(self, user_id: uuid.UUID) -> None:
        if self.fail_delete:
            raise AuthAdminError("fake delete_user failure")
        self.deleted.append(user_id)
        self.users.discard(user_id)
