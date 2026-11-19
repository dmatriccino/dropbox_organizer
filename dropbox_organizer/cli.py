"""Command line entry point: auth, scan, report."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dropbox_organizer import config
from dropbox_organizer.media import DEFAULT_EXCLUDED_DIRS

log = logging.getLogger("dropbox_organizer")


def _parse_dirs(value: str) -> tuple[str, ...]:
    return tuple(d.strip() for d in value.split(",") if d.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dropbox_organizer",
        description="Read-only Dropbox duplicate-media report. Cannot modify your Dropbox.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get(config.DB_PATH_ENV, config.DEFAULT_DB_PATH)),
        help="SQLite checkpoint file (default: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="one-time PKCE login; saves a read-only refresh token")

    scan = sub.add_parser("scan", help="resumable metadata scan into SQLite")
    scan.add_argument("--fresh", action="store_true", help="discard any checkpoint and start over")
    scan.add_argument(
        "--exclude-dirs",
        type=_parse_dirs,
        default=DEFAULT_EXCLUDED_DIRS,
        metavar="A,B,C",
        help=f"comma-separated folder names to skip (replaces the default: "
        f'{",".join(DEFAULT_EXCLUDED_DIRS)}). Pass "" to exclude nothing.',
    )

    report = sub.add_parser("report", help="write duplicates CSV + summary from SQLite")
    report.add_argument(
        "--reports-dir",
        type=Path,
        default=Path(os.environ.get(config.REPORTS_DIR_ENV, config.DEFAULT_REPORTS_DIR)),
        help="output folder (default: %(default)s)",
    )
    return parser


def cmd_auth() -> int:
    from dropbox_organizer.auth import SCOPES, run_auth_flow
    from dropbox_organizer.client import ReadOnlyDropbox

    app_key = config.get_app_key()
    print("Requesting scopes: " + " ".join(SCOPES))
    refresh_token = run_auth_flow(app_key)
    path = config.save_refresh_token(refresh_token)
    print(f"Saved refresh token to {path} (mode 600).")
    account = ReadOnlyDropbox.from_refresh_token(refresh_token, app_key).users_get_current_account()
    print(f"Logged in as {account.name.display_name} <{account.email}>.")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from dropbox_organizer.client import ReadOnlyDropbox
    from dropbox_organizer.scan import run_scan
    from dropbox_organizer.store import ScanStore

    client = ReadOnlyDropbox.from_refresh_token(config.load_refresh_token(), config.get_app_key())
    account = client.users_get_current_account()
    log.info("Scanning Dropbox of %s (metadata only).", account.name.display_name)
    with ScanStore(args.db) as store:
        try:
            result = run_scan(client, store, excluded_dirs=args.exclude_dirs, fresh=args.fresh)
        except KeyboardInterrupt:
            log.warning(
                "Interrupted. Progress is checkpointed in %s; run scan again to resume.", args.db
            )
            return 130
    log.info(
        "Done: %d pages, %d files, %d media kept, %d media excluded.",
        result.pages,
        result.files_seen,
        result.media_kept,
        result.excluded_media,
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from dropbox_organizer.report import human_bytes, write_report
    from dropbox_organizer.store import ScanStore

    if not args.db.exists():
        raise config.ConfigError(f"No scan database at {args.db}. Run `scan` first.")
    with ScanStore(args.db) as store:
        csv_path, summary_path, summary = write_report(store, args.reports_dir)
    print(summary.render())
    print(f"Wrote {csv_path}")
    print(f"Wrote {summary_path}")
    print(
        f"{summary.duplicate_groups} duplicate groups, "
        f"{human_bytes(summary.reclaimable_bytes)} reclaimable."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.command == "auth":
            return cmd_auth()
        if args.command == "scan":
            return cmd_scan(args)
        if args.command == "report":
            return cmd_report(args)
    except config.ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover
