"""seed-pool: fill the shared pool with one real LinkedIn collection run under the OWNER's
Apify token, so the first Students never see an empty feed (FR-6, AD-12).

Usage (from ``api/``)::

    SEED_APIFY_TOKEN=apify_api_... uv run python scripts/seed_pool.py \\
        --role "Backend Engineering Intern" --location "Bengaluru, Karnataka, India" \\
        [--role ...] [--location ...] [--job-type internship] [--max-items 50] \
        [--max-charge-usd 0.10]

Refuses to run (exit code 2) without ``SEED_APIFY_TOKEN``. The run goes through the one spend
path, ``listings.service.start_provider_run`` (the System actor: ``owner_id`` NULL,
``run_key`` ``seed:<uuid>``, every Listing contributes to the pool), with the same actor
allowlist, caps and kill switch as Student runs. Unlike a job, a CLI may wait: the script
polls the run itself (sleeping between polls) through ``poll_provider_run`` and ingests
through the same pipeline. It prints the counts and the cost. The token is never printed
or stored. Uses DATABASE_URL via Settings, like the API.
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import apify  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.modules.listings import service  # noqa: E402
from app.ports.provider_runner import ProviderUnavailable, install_provider_runner  # noqa: E402
from app.ports.source_adapter import install_provider_source  # noqa: E402

DEFAULT_POLL_SECONDS = 30
MAX_SEED_CHARGE_USD = 2.0
# About what the primary actor costs per result (USD 1 per 1,000).
USD_PER_ITEM = 0.001
MAX_CONSECUTIVE_UNAVAILABLE = 10


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_pool", description="Seed the shared Listing pool with one Apify run."
    )
    parser.add_argument("--role", action="append", required=True, help="a role title (repeat)")
    parser.add_argument("--location", action="append", default=[], help="a location (repeat)")
    parser.add_argument(
        "--job-type",
        action="append",
        default=[],
        choices=("internship", "full_time"),
        help="a job type (repeat; default any)",
    )
    parser.add_argument(
        "--max-items", type=int, default=None, help="item cap (default COLLECT_MAX_ITEMS)"
    )
    parser.add_argument(
        "--max-charge-usd",
        type=float,
        default=None,
        help=f"charge cap, 0 < x <= {MAX_SEED_CHARGE_USD} (default COLLECT_MAX_CHARGE_USD)",
    )
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    if args.max_items is not None and not 1 <= args.max_items <= 1000:
        parser.error("--max-items must be 1..1000")
    if args.max_charge_usd is not None and not 0 < args.max_charge_usd <= MAX_SEED_CHARGE_USD:
        parser.error(f"--max-charge-usd must be > 0 and <= {MAX_SEED_CHARGE_USD}")
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    return args


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    token = settings.seed_apify_token
    if token is None:
        print("seed-pool: SEED_APIFY_TOKEN is not set; refusing to run", file=sys.stderr)
        return 2
    max_items = args.max_items if args.max_items is not None else settings.collect_max_items
    charge = (
        args.max_charge_usd
        if args.max_charge_usd is not None
        else settings.collect_max_charge_usd
    )
    affordable = int(charge / USD_PER_ITEM)
    if max_items > affordable:
        print(
            f"seed-pool: warning: {max_items} items may exceed what USD {charge} buys at about "
            f"USD 1 per 1,000 (~{affordable}); the run stops at the charge cap",
            file=sys.stderr,
        )
    install_provider_runner(apify.from_settings())
    install_provider_source(apify.ApifyLinkedInSource())
    params = {"role_titles": args.role, "locations": args.location, "job_types": args.job_type}

    outcome = await service.start_provider_run(
        None,
        service.PURPOSE_JOBS,
        params,
        run_key=f"seed:{uuid.uuid4()}",
        token=token,
        max_items=max_items,
        max_charge_usd=charge,
    )
    if outcome.status != "running":
        print(f"seed-pool: run {outcome.status} ({outcome.error_kind}); nothing collected")
        return 1
    print(f"seed-pool: run {outcome.run_id} started; polling every {args.poll_seconds} s")

    deadline = time.monotonic() + service.RUN_TIMEOUT.total_seconds() + 600
    unavailable = 0
    while outcome.status == "running":
        if time.monotonic() > deadline:
            print("seed-pool: gave up waiting; the run row stays 'running'", file=sys.stderr)
            return 1
        await asyncio.sleep(args.poll_seconds)
        try:
            outcome = await service.poll_provider_run(
                outcome.run_id, token=token, reschedule=False
            )
            unavailable = 0
        except ProviderUnavailable:
            unavailable += 1
            print(f"seed-pool: Apify unavailable ({unavailable}); retrying", file=sys.stderr)
            if unavailable >= MAX_CONSECUTIVE_UNAVAILABLE:
                return 1

    async with get_sessionmaker()() as session:
        row = await service.get_provider_run(session, outcome.run_id)
    if row is None:
        print(f"seed-pool: run {outcome.run_id} is gone; nothing to report", file=sys.stderr)
        return 1
    cost = f"{row.cost_usd} USD" if row.cost_usd is not None else "unknown"
    print(
        f"seed-pool: run {row.status}"
        + (f" ({row.error_kind})" if row.error_kind else "")
        + f"; items={row.items or 0} ingested={row.ingested or 0} "
        f"rejected={row.rejected or 0} cost={cost}"
    )
    return 0 if row.status == "succeeded" else 1


async def _main(args: argparse.Namespace) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)  # first, so --help works without a token
    if get_settings().seed_apify_token is None:
        print("seed-pool: SEED_APIFY_TOKEN is not set; refusing to run", file=sys.stderr)
        return 2
    # psycopg's async driver cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_main(args), loop_factory=loop_factory)


if __name__ == "__main__":
    raise SystemExit(main())
