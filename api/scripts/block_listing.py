"""block-listing: the owner's takedown of a Listing, URL or company (FR-8, NFR-7).

Usage (from ``api/``)::

    uv run python scripts/block_listing.py block   --listing linkedin:3692563201
    uv run python scripts/block_listing.py block   --url "https://www.linkedin.com/jobs/view/1?trk=x"
    uv run python scripts/block_listing.py block   --company "Acme Corp"
    uv run python scripts/block_listing.py unblock --company "Acme Corp"

``block`` records the takedown, deletes every matching Listing now (pinned ones too: a takedown
wins) and keeps matching drafts from ever being ingested again. Both actions write an
``admin_audit`` row (``listings.block`` / ``listings.unblock``, ``actor_id`` NULL = the CLI) in
the same transaction. ``unblock`` of a takedown that does not exist changes nothing (exit 1).

Runs only with ``ENVIRONMENT`` local or ci unless ``--yes-prod`` is given. Uses DATABASE_URL via
Settings, like the API. Exit codes: 0 done, 1 unblock found nothing, 2 bad usage or refused.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.modules.listings import service  # noqa: E402

ALLOWED_ENVIRONMENTS = ("local", "ci")


async def run(action: str, kind: str, value: str) -> int:
    """Apply ``action`` (``block`` or ``unblock``) and return the process exit code. Prints to
    stdout / stderr."""
    async with get_sessionmaker()() as session:
        try:
            if action == "block":
                removed = await service.block(session, kind, value)
            else:
                lifted = await service.unblock(session, kind, value)
        except ValueError as exc:
            await session.rollback()
            print(f"block-listing: {exc}", file=sys.stderr)
            return 2
        if action == "unblock" and not lifted:
            await session.rollback()
            print(f"block-listing: no {kind} takedown for that value; nothing changed")
            return 1
        await session.commit()
    if action == "block":
        print(f"block-listing: blocked the {kind}; {removed} Listing(s) deleted")
    else:
        print(f"block-listing: lifted the {kind} takedown")
    return 0


async def _main(action: str, kind: str, value: str) -> int:
    try:
        return await run(action, kind, value)
    finally:
        await dispose_engine()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="block-listing", description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("block", "unblock"))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--listing", metavar="SOURCE:ID", help="one Listing, e.g. linkedin:123")
    target.add_argument("--url", help="a Listing URL (the query and fragment are ignored)")
    target.add_argument("--company", help="a company name (case-insensitive)")
    parser.add_argument(
        "--yes-prod",
        action="store_true",
        help="allow running with ENVIRONMENT=prod (local and ci only otherwise)",
    )
    args = parser.parse_args(argv)
    for kind in ("listing", "url", "company"):
        value = getattr(args, kind)
        if value is not None:
            if not value.strip():
                parser.error(f"--{kind} must not be empty")
            args.kind, args.value = kind, value
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    environment = get_settings().environment
    if environment not in ALLOWED_ENVIRONMENTS and not args.yes_prod:
        print(
            f"block-listing: refusing to run with ENVIRONMENT={environment} without --yes-prod",
            file=sys.stderr,
        )
        return 2
    # psycopg's async driver cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_main(args.action, args.kind, args.value), loop_factory=loop_factory)


if __name__ == "__main__":
    raise SystemExit(main())
