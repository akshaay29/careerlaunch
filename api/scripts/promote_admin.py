"""promote-admin: make an existing account an admin (AD-5). The ONLY way to grant the role.

Usage (from ``api/``)::

    uv run python scripts/promote_admin.py <email>

The account must already exist (the person signs up and opens the app once). In one
transaction the role is set to ``admin`` and an ``admin_audit`` row is written
(``action='account.promote_admin'``, ``actor_id`` NULL = system). An unknown email exits 1 and
changes nothing; an account that is already an admin is left as is (exit 0, no audit row).
Uses DATABASE_URL via Settings, like the API.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.core.errors import NotFoundError  # noqa: E402
from app.modules.accounts.service import promote_admin  # noqa: E402


async def run(email: str) -> int:
    """Promote ``email``; returns the process exit code. Prints to stdout / stderr."""
    async with get_sessionmaker()() as session:
        try:
            actor, changed = await promote_admin(session, email)
        except NotFoundError:
            await session.rollback()
            print(
                f"promote-admin: no account with email {email.strip().lower()!r} "
                "(the person must sign up and open the app first)",
                file=sys.stderr,
            )
            return 1
        if not changed:
            await session.rollback()
            print(f"promote-admin: {actor.email} ({actor.id}) is already an admin; nothing changed")
            return 0
        await session.commit()
    print(f"promote-admin: {actor.email} ({actor.id}) is now an admin")
    return 0


async def _main(email: str) -> int:
    try:
        return await run(email)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promote-admin", description=__doc__.splitlines()[0])
    parser.add_argument("email", help="email of an existing account")
    args = parser.parse_args(argv)
    if not args.email.strip():
        parser.error("email must not be empty")
    # psycopg's async driver cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_main(args.email), loop_factory=loop_factory)


if __name__ == "__main__":
    raise SystemExit(main())
