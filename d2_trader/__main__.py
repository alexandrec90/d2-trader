"""`python -m d2_trader [serve|worker|run <job>|rune-value <rune>]`.

`serve` is the default, because the image's command names no subcommand.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime

import uvicorn

from d2_trader.config import get_settings
from d2_trader.currency import RUNES, normalize_currency
from d2_trader.db import make_engine, make_sessions
from d2_trader.markets import GAME_VERSIONS, MODES, PLATFORMS, MarketKey
from d2_trader.models import SourceRun
from d2_trader.release import started_release, watch_in_background
from d2_trader.services.calendar import ensure_seed_seasons, load_seasons
from d2_trader.services.rune_values import fit_market, report
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


async def rune_value_lines(rune: str, market: MarketKey, days: int) -> list[str]:
    settings = get_settings()
    sessions = make_sessions(make_engine(settings.database_url))
    async with sessions() as session:
        await ensure_seed_seasons(session)
        seasons = await load_seasons(session)
        fit = await fit_market(session, market, seasons, datetime.now(UTC))
    return report(fit, rune, market, days)


async def run_worker() -> None:
    settings = get_settings()
    sessions = make_sessions(make_engine(settings.database_url))
    await run_forever(sessions, settings, started_release().stale)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="d2_trader")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="the dashboard")
    commands.add_parser("worker", help="the collection schedule")
    once = commands.add_parser("run", help="one collection job, now")
    once.add_argument(
        "job", choices=["seasons", "catalog", "diablo2io", "traderie", "rates", "goods"]
    )
    value = commands.add_parser("rune-value", help="one rune's modelled value in a market")
    value.add_argument("rune", type=normalize_currency, choices=RUNES)
    value.add_argument("--platform", choices=PLATFORMS, default="PC")
    value.add_argument("--mode", choices=MODES, default="softcore")
    value.add_argument("--non-ladder", action="store_true")
    value.add_argument("--version", choices=sorted(GAME_VERSIONS), default="rotw")
    value.add_argument("--days", type=int, default=14, help="days of history to print")
    return parser


def market_from(args: argparse.Namespace) -> MarketKey:
    return MarketKey(args.platform, args.mode, not args.non_ladder, args.version)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "worker":
        run_async(run_worker())
    elif args.command == "run":
        run = run_async(run_named_job(args.job))
        print(f"{run.job}: {run.status} ({run.records} records) {run.detail or ''}".rstrip())
        return 0 if run.status in ("ok", "disabled") else 1
    elif args.command == "rune-value":
        print("\n".join(run_async(rune_value_lines(args.rune, market_from(args), args.days))))
    else:
        watch_in_background(started_release())
        uvicorn.run(
            create_app,
            factory=True,
            host="0.0.0.0",  # noqa: S104 - inside a container
            port=8000,
            timeout_graceful_shutdown=10,  # an open page must not hold up a deploy
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
