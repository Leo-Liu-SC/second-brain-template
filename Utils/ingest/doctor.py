#!/usr/bin/env python3
"""Operational health checks for the Second Brain workflow."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lint import LintReport, check_wiki_links


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "raw"
WIKI_DIR = ROOT / "wiki"
DAILY_DIR = ROOT / "daily"
LOG_DIR = ROOT / "Utils" / "ingest" / "logs"
CAPTURE_LOG = ROOT / "scripts" / "capture.log"


@dataclass
class Check:
    level: str
    name: str
    detail: str


def newest_file(directory: Path, pattern: str = "*") -> Path | None:
    files = [path for path in directory.glob(pattern) if path.is_file()] if directory.exists() else []
    return max(files, key=lambda path: path.stat().st_mtime, default=None)


def scheduler_installed() -> bool | None:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5, check=False
        )
    except PermissionError:
        return None
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "# second-brain-ingest" in result.stdout


def collect_checks() -> list[Check]:
    checks: list[Check] = []

    for name, directory in (("raw", RAW_DIR), ("wiki", WIKI_DIR), ("daily", DAILY_DIR)):
        level = "ok" if directory.is_dir() else "error"
        checks.append(Check(level, f"{name} directory", str(directory)))

    scheduler = scheduler_installed()
    if scheduler is True:
        checks.append(Check("ok", "scheduler", "two tagged ingestion jobs are installed"))
    elif scheduler is None:
        checks.append(Check("warn", "scheduler", "could not inspect crontab in this environment"))
    else:
        checks.append(Check("error", "scheduler", "not installed; run Utils/ingest/install_cron.sh"))

    ingestion_logs = []
    if LOG_DIR.exists():
        for path in LOG_DIR.glob("*.log"):
            content = path.read_text(encoding="utf-8", errors="replace")
            if "[dry]" not in content and path.name != "cron.log" and not path.name.startswith("zoom_error_"):
                ingestion_logs.append(path)
    latest_run = max(ingestion_logs, key=lambda path: path.stat().st_mtime, default=None)
    if latest_run:
        age_hours = (datetime.now().timestamp() - latest_run.stat().st_mtime) / 3600
        content = latest_run.read_text(encoding="utf-8", errors="replace")
        failed = "FAILED" in content or "NEEDS RE-AUTH" in content
        level = "error" if failed else ("warn" if age_hours > 36 else "ok")
        detail = f"{latest_run.name}, {age_hours:.1f}h ago"
        if failed:
            detail += "; contains a source failure or re-auth request"
        checks.append(Check(level, "last ingestion", detail))
    else:
        checks.append(Check("error", "last ingestion", "no ingestion logs found"))

    latest_raw = newest_file(RAW_DIR, "**/*.md")
    latest_wiki = newest_file(WIKI_DIR, "*.md")
    if latest_raw and latest_wiki:
        raw_newer = sum(
            path.stat().st_mtime > latest_wiki.stat().st_mtime
            for path in RAW_DIR.rglob("*.md")
        )
        level = "warn" if raw_newer else "ok"
        checks.append(Check(level, "wiki freshness", f"{raw_newer} raw files are newer than the latest wiki update"))

    link_report = LintReport()
    check_wiki_links(link_report)
    level = "error" if link_report.broken_links else "ok"
    checks.append(Check(level, "wiki links", f"{len(link_report.broken_links)} broken links"))

    latest_daily = newest_file(DAILY_DIR, "*.md")
    if latest_daily:
        checks.append(Check("ok", "session memory", f"latest daily log: {latest_daily.name}"))
    else:
        checks.append(Check("warn", "session memory", "no daily logs found"))

    latest_inbox = newest_file(DAILY_DIR / "inbox", "*.md")
    if latest_inbox:
        checks.append(Check("ok", "conversation inbox", f"latest local capture: {latest_inbox.name}"))
    else:
        checks.append(Check("warn", "conversation inbox", "no local captures yet"))

    if CAPTURE_LOG.exists():
        tail = CAPTURE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        outcomes = [line for line in tail if " ERROR " in line or "Captured session" in line]
        latest_outcome = outcomes[-1] if outcomes else "no local capture outcome found"
        level = "warn" if " ERROR " in latest_outcome else "ok"
        checks.append(Check(level, "local capture", latest_outcome[:180]))

    return checks


def main() -> int:
    icons = {"ok": "✓", "warn": "!", "error": "✗"}
    checks = collect_checks()
    print("Second Brain doctor")
    print("=" * 50)
    for check in checks:
        print(f"{icons[check.level]} {check.name}: {check.detail}")
    errors = sum(check.level == "error" for check in checks)
    warnings = sum(check.level == "warn" for check in checks)
    print(f"\n{errors} errors, {warnings} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
