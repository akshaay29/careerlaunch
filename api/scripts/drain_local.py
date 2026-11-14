"""Run the job drain locally, without HTTP (AD-4 local environment).

Usage (from ``api/``)::

    uv run python scripts/drain_local.py              # one drain
    uv run python scripts/drain_local.py --every 300  # drain every 5 minutes until Ctrl+C

Uses DATABASE_URL via Settings, like the API. Same advisory lock as the HTTP drain, so it is
safe to run next to a local ``uvicorn``.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import supabase_auth, supabase_storage  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine  # noqa: E402
from app.core.jobs import QUEUES, drain  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.ports.auth_admin import LazyAuthAdmin, install_auth_admin  # noqa: E402
from app.ports.file_store import LazyFileStore, install_file_store  # noqa: E402


async def _main(every: float | None, queues: tuple[str, ...]) -> int:
    try:
        while True:
            result = await drain(queues=queues)
            print(f"drain {result.status}: {result.counts()}", flush=True)
            if every is None:
                return 0
            await asyncio.sleep(every)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--every", type=float, default=None, help="repeat every N seconds")
    parser.add_argument(
        "--queue", action="append", choices=QUEUES, help="queue(s) to work, in order (default: all)"
    )
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    # Tasks such as profile.delete_stored_files reach Storage through the installed FileStore.
    install_file_store(LazyFileStore(supabase_storage.from_settings))
    # accounts.delete_auth_user reaches Supabase Auth through the installed AuthAdmin.
    install_auth_admin(LazyAuthAdmin(supabase_auth.from_settings))
    queues = tuple(args.queue) if args.queue else QUEUES
    # psycopg's async driver cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        return asyncio.run(_main(args.every, queues), loop_factory=loop_factory)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
