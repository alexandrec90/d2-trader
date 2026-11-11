"""`python -m d2_trader [serve|worker|run <job>]`.

`serve` is the default, because the image's command names no subcommand.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Coroutine

import uvicorn

from d2_trader.config import get_settings
from d2_trader.db import make_engine, make_sessions
from d2_trader.models import SourceRun
from d2_trader.services.calendar import ensure_seed_seasons
from d2_trader.services.worker import job_specs, run_forever, run_job
from d2_trader.web.app import create_app


def run_async(coroutine: Coroutine):
    """psycopg's async mode cannot use Windows' default loop, so pick one that it can."""
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coroutine, loop_factory=factory)


async def run_named_job(name: str) -> SourceRun:
    settings = get_settings()
    sessions = make_sessions(make_engine(settings.database_url))
    specs = {spec.name: spec for spec in job_specs(settings)}
    async with sessions() as session, session.begin():
        await ensure_seed_seasons(session)
    return await run_job(sessions, specs[name], settings)


async def run_worker() -> None:
    settings = get_settings()
    await run_forever(make_sessions(make_engine(settings.database_url)), settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="d2_trader")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="the dashboard")
    commands.add_parser("worker", help="the collection schedule")
    once = commands.add_parser("run", help="one collection job, now")
    once.add_argument("job", choices=["seasons", "catalog", "diablo2io", "traderie", "rates"])
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "worker":
        run_async(run_worker())
    elif args.command == "run":
        run = run_async(run_named_job(args.job))
        print(f"{run.job}: {run.status} ({run.records} records) {run.detail or ''}".rstrip())
        return 0 if run.status in ("ok", "disabled") else 1
    else:
        uvicorn.run(create_app, factory=True, host="0.0.0.0", port=8000)  # noqa: S104 - inside a container
    return 0


if __name__ == "__main__":
    sys.exit(main())
