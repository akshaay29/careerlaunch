"""ensure-bucket: make sure the PRIVATE Storage bucket exists (hosted Supabase).

Usage (from ``api/``)::

    uv run python scripts/ensure_bucket.py

Idempotent: creates ``STORAGE_BUCKET`` (default ``private``) as a private bucket with a 5 MiB
file size limit and PDF/DOCX/plain text as the only allowed MIME types when it is missing,
and resets an existing bucket to private with that limit and those types if it differs. It never makes a bucket public. Uses ``SUPABASE_URL`` and
``SUPABASE_SERVICE_ROLE_KEY`` via Settings. Runs in migrate-deploy.yml after migrations; the
local CLI stack declares the same bucket in ``supabase/config.toml``.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.supabase_storage import from_settings  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.modules.profile.files import DOCX, MAX_FILE_BYTES, PDF, TEXT  # noqa: E402
from app.ports.file_store import FileStoreError  # noqa: E402

# The same list as [storage.buckets.private] in supabase/config.toml.
ALLOWED_MIME_TYPES = [PDF, DOCX, TEXT]


async def run() -> int:
    settings = get_settings()
    try:
        store = from_settings(settings)
    except RuntimeError as exc:
        print(f"ensure-bucket: {exc}", file=sys.stderr)
        return 1
    try:
        outcome = await store.ensure_bucket(
            file_size_limit=MAX_FILE_BYTES, allowed_mime_types=ALLOWED_MIME_TYPES
        )
    except FileStoreError as exc:
        print(f"ensure-bucket: {exc}", file=sys.stderr)
        return 1
    print(f"ensure-bucket: bucket {settings.storage_bucket!r} is private ({outcome})")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
