#!/usr/bin/env python3
"""
Second Brain content ingestion CLI.

Usage:
    python ingest.py confluence <url-or-page-id>
    python ingest.py web <url>
    python ingest.py slack <channel> [--days 7]
    python ingest.py zoom [--days 30]
    python ingest.py all [--days 7]
    python ingest.py doctor
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def cmd_confluence(args):
    from sources.confluence import fetch
    out = fetch(args.url_or_id)
    print(f"✓ Confluence → {out}")


def cmd_web(args):
    from sources.web import fetch
    out = fetch(args.url)
    print(f"✓ Web → {out}")


def cmd_douyin(args):
    from sources.douyin import fetch
    out = fetch(args.url_or_text, transcribe=not args.no_transcript)
    print(f"✓ Douyin → {out}")


def cmd_slack(args):
    from sources.slack import fetch
    out, _ = fetch(args.channel, days=args.days, login=args.login)
    if out:
        print(f"✓ Slack → {out}")
    else:
        print("✓ Slack: no new messages")


def cmd_zoom(args):
    from sources.zoom import fetch, NeedsReauth
    try:
        summary, new_files = fetch(days=args.days, login=getattr(args, "login", False), headless=not getattr(args, "login", False))
        print(f"✓ Zoom → {summary}")
    except NeedsReauth as e:
        print(f"✗ Zoom needs re-auth: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_all(args):
    print("Running all sources...\n")
    print("── Zoom ──")
    cmd_zoom(args)

    print("\nNote: confluence and web require explicit URLs.")
    print("      Run them individually: python ingest.py confluence <url>")
    print("      Run them individually: python ingest.py web <url>")

    if hasattr(args, "slack_channel") and args.slack_channel:
        print("\n── Slack ──")
        args.channel = args.slack_channel
        cmd_slack(args)


def cmd_doctor(args):
    from doctor import main as doctor_main
    raise SystemExit(doctor_main())


def main():
    parser = argparse.ArgumentParser(description="Second Brain content ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    # confluence
    p = sub.add_parser("confluence", help="Fetch a Confluence page")
    p.add_argument("url_or_id", help="Confluence page URL or numeric ID")
    p.set_defaults(func=cmd_confluence)

    # web
    p = sub.add_parser("web", help="Fetch a web page")
    p.add_argument("url", help="Web page URL")
    p.set_defaults(func=cmd_web)

    # douyin
    p = sub.add_parser("douyin", help="Fetch a Douyin video (URL or raw share text)")
    p.add_argument("url_or_text", help="Douyin URL or full share text pasted from the app")
    p.add_argument("--no-transcript", action="store_true", help="Skip Whisper transcription (metadata only)")
    p.set_defaults(func=cmd_douyin)

    # slack
    p = sub.add_parser("slack", help="Fetch Slack channel messages")
    p.add_argument("channel", help="Channel ID (C...) or full app.slack.com URL")
    p.add_argument("--days", type=int, default=7, help="How many days back (default: 7)")
    p.add_argument("--login", action="store_true", help="Force interactive SSO login")
    p.set_defaults(func=cmd_slack)

    # zoom
    p = sub.add_parser("zoom", help="Sync Zoom meeting notes")
    p.add_argument("--days", type=int, default=30, help="How many days back (default: 30)")
    p.add_argument("--login", action="store_true", help="Force interactive SSO login")
    p.set_defaults(func=cmd_zoom)

    # all
    p = sub.add_parser("all", help="Run zoom + optional slack")
    p.add_argument("--days", type=int, default=7, help="Days back for Slack (default: 7)")
    p.add_argument("--slack-channel", dest="slack_channel", help="Also fetch this Slack channel")
    p.set_defaults(func=cmd_all)

    # doctor
    p = sub.add_parser("doctor", help="Check ingestion, scheduler, wiki, and memory health")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
