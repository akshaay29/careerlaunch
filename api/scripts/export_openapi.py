"""Export the FastAPI OpenAPI schema (the AD-14 contract) to web/openapi.json.

Usage: ``uv run python scripts/export_openapi.py [output_path]``. Needs no database.
"""

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

from app.main import create_app  # noqa: E402

DEFAULT_OUT = API_DIR.parent / "web" / "openapi.json"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    schema = create_app().openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
