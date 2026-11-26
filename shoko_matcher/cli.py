"""Command line interface for the Shoko filename matcher."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from .client import ShokoAuthError, ShokoClient, ShokoError
from .config import ENV_CONFIG, Config, ConfigError, load_config
from .reporter import print_report, write_report
from .runner import run_matching

LOG = logging.getLogger("shoko_matcher")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shoko-matcher",
        description=(
            "Find unrecognized files in Shoko Server and link them to the "
            "correct episodes. Dry-run by default; writes require --apply."
        ),
    )
    parser.add_argument("--base-url", help="Shoko base URL (env SHOKO_URL)")
    parser.add_argument("--apikey", help="Shoko API key (env SHOKO_APIKEY)")
    parser.add_argument("--apikey-file", help="File containing the API key")
    parser.add_argument("--config", help="Path to a JSON config file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Only GET, never POST (default)"
    )
    mode.add_argument(
        "--apply", action="store_true", help="Actually create links (POST)"
    )
    parser.add_argument(
        "--min-confidence", type=float, help="Minimum score to auto-link (0-100)"
    )
    parser.add_argument("--series", help="Only handle series matching this name")
    parser.add_argument(
        "--import-folder", type=int, help="Only files in this import folder ID"
    )
    parser.add_argument("--limit", type=int, help="Process at most N files")
    parser.add_argument("--report", help="Write a CSV or JSON report to this path")
    parser.add_argument(
        "--create-missing-series",
        action="store_true",
        help="Allow queuing AniDB refreshes that create missing series",
    )
    parser.add_argument(
        "--episode-id-type",
        choices=["shoko", "anidb"],
        help="ID type used in /File/{id}/Link (default shoko)",
    )
    parser.add_argument(
        "--link-method",
        choices=["episode", "range"],
        help="Link by episode IDs or LinkFromSeries range (default episode)",
    )
    parser.add_argument("--timeout", type=float, help="HTTP timeout in seconds")
    parser.add_argument("--max-retries", type=int, help="Max HTTP retries")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase log verbosity"
    )
    return parser


def run(config: Config, client: ShokoClient) -> int:
    summary = run_matching(config, client)
    print_report(
        sys.stdout,
        summary.decisions,
        summary.outcomes,
        summary.baseline,
        summary.after,
    )
    if config.report_path:
        write_report(config.report_path, summary.decisions, summary.outcomes)
        print(f"Report written to {config.report_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.config:
        os.environ[ENV_CONFIG] = args.config
    try:
        config = load_config(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with ShokoClient(config) as client:
            return run(config, client)
    except ShokoAuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 3
    except ShokoError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
