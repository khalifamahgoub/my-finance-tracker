"""Command-line entry point. One command does the whole job: `finance run`.

    finance run            process inbox + regenerate dashboard (the one entry point)
    finance init           bootstrap the database + seed reference data
    finance review         interactively resolve uncategorised transactions
    finance period <name>  regenerate an old month (e.g. "Feb 2026" or 2026-02)
    finance sync-notion    push a projection to the Notion hub (Phase 4)
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import Config
from . import db as dbm


def cmd_init(args: argparse.Namespace) -> int:
    cfg = Config.load()
    conn = dbm.init_db(cfg)
    tables = dbm.table_names(conn)
    n_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    n_ibans = conn.execute("SELECT COUNT(*) FROM iban_map").fetchone()[0]
    n_review = conn.execute(
        "SELECT COUNT(*) FROM iban_map WHERE flag='REVIEW'").fetchone()[0]
    print(f"Initialised {cfg.db_path}")
    print(f"  tables ({len(tables)}): {', '.join(tables)}")
    print(f"  categories seeded: {n_cats}")
    print(f"  iban_map seeded:   {n_ibans}  (REVIEW-flagged: {n_review})")
    conn.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load()
    dbm.init_db(cfg).close()          # ensure schema + seeds are current
    try:
        from . import ingest
    except ImportError:
        print("ingest pipeline not available yet (Phase 1).")
        return 0
    return ingest.run(cfg, sync=getattr(args, "sync", False),
                      narrate=getattr(args, "narrate", False))


def cmd_review(args: argparse.Namespace) -> int:
    from . import review
    return review.run_review(Config.load(), limit=args.limit)


def cmd_period(args: argparse.Namespace) -> int:
    from . import report_html
    from .periods import parse_period
    cfg = Config.load()
    period_id = parse_period(args.name)
    out = cfg.output / "period" / period_id
    html_path, _ = report_html.generate(cfg, explicit_period=args.name, into=out)
    print(f"Regenerated {period_id}: {html_path}")
    return 0


def cmd_sync_notion(args: argparse.Namespace) -> int:
    from . import notion_sync
    cfg = Config.load()
    if getattr(args, "pull", False):
        return notion_sync.pull_review(cfg, dry_run=args.dry_run)
    return notion_sync.sync(cfg, dry_run=args.dry_run)


def cmd_narrate(args: argparse.Namespace) -> int:
    from . import narrate
    return narrate.narrate(Config.load(), period=args.period, dry_run=args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finance", description="Personal finance tracker (Bahrain, tri-account).")
    parser.add_argument("--version", action="version", version=f"finance {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="bootstrap the database + seed reference data").set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="process inbox + regenerate dashboard")
    p_run.add_argument("--sync", action="store_true",
                       help="after generating the dashboard, push the projection to Notion")
    p_run.add_argument("--narrate", action="store_true",
                       help="also generate the AI narrative (needs ANTHROPIC_API_KEY)")
    p_run.set_defaults(func=cmd_run)

    p_review = sub.add_parser("review", help="resolve uncategorised transactions")
    p_review.add_argument("--limit", type=int, default=25, help="max groups to review")
    p_review.set_defaults(func=cmd_review)

    p_period = sub.add_parser("period", help="regenerate an old month")
    p_period.add_argument("name", help="period, e.g. 'Feb 2026' or 2026-02")
    p_period.set_defaults(func=cmd_period)

    p_sync = sub.add_parser("sync-notion", help="push a projection to the Notion hub")
    p_sync.add_argument("--dry-run", action="store_true", help="print what would sync; touch nothing")
    p_sync.add_argument("--pull", action="store_true",
                        help="read Review Queue Category tags back into SQLite, then re-categorise")
    p_sync.set_defaults(func=cmd_sync_notion)

    p_narr = sub.add_parser("narrate", help="AI 3-line summary of a period (Claude API)")
    p_narr.add_argument("period", nargs="?", default=None, help="period, e.g. 'Feb 2026' (default: current)")
    p_narr.add_argument("--dry-run", action="store_true", help="print the prompt; call nothing")
    p_narr.set_defaults(func=cmd_narrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
