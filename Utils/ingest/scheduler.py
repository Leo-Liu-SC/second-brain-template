#!/usr/bin/env python3
"""
Daily ingestion scheduler.

Reads URLs from `source_urls.md` and routes each to the right fetcher.
Slack and Zoom run incrementally via `state.json` — only new content.

Usage:
    python3 scheduler.py [--dry-run]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

SOURCE_FILE = ROOT / "source_urls.md"
STATE_FILE = ROOT / "state.json"
LOG_DIR = ROOT / "logs"

# Slack channels to incrementally sync each run.
# Loaded from SLACK_CHANNELS env var (JSON): '{"slackbot": "https://app.slack.com/client/T.../D..."}'
# Falls back to empty dict if not set — add channels via setup.sh or edit .env directly.
def _load_slack_channels() -> dict:
    raw = os.environ.get("SLACK_CHANNELS", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] SLACK_CHANNELS env var is not valid JSON — Slack sync disabled", file=sys.stderr)
        return {}

SLACK_CHANNELS = _load_slack_channels()

URL_RE = re.compile(r"https?://[^\s)<>]+")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"urls_done": [], "slack_ts": {}, "zoom_last_run": None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def parse_source_urls() -> list[str]:
    if not SOURCE_FILE.exists():
        return []
    urls = []
    for line in SOURCE_FILE.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue  # skip blanks and comments (commented-out URLs included)
        m = URL_RE.search(line)
        if m:
            urls.append(m.group(0))
    return urls


def classify(url: str) -> str:
    if "atlassian.net/wiki" in url:
        return "confluence"
    if "youtube.com" in url or "youtu.be" in url:
        return "web"  # web.py auto-detects YouTube
    return "web"


def run_url(url: str, dry: bool):
    kind = classify(url)
    if dry:
        return True, f"[dry] {kind}: {url}", None
    try:
        if kind == "confluence":
            from sources.confluence import fetch as fetch_conf
            out = fetch_conf(url)
        else:
            from sources.web import fetch as fetch_web
            out = fetch_web(url)
        return True, f"{kind}: {out.name}", str(out)
    except Exception as e:
        return False, f"{kind} FAILED ({url}): {e}", None


def run_slack(state: dict, dry: bool):
    msgs = []
    new_files = []
    if dry:
        for label, ch in SLACK_CHANNELS.items():
            since = state["slack_ts"].get(label, "(initial 7 days)")
            msgs.append(f"[dry] slack {label}: since {since}")
        return msgs, new_files

    from sources.slack import fetch as fetch_slack
    for label, ch in SLACK_CHANNELS.items():
        try:
            oldest = state["slack_ts"].get(label)
            if oldest:
                out, latest = fetch_slack(ch, oldest_ts=float(oldest) + 0.000001)
            else:
                out, latest = fetch_slack(ch, days=7)
            if latest:
                state["slack_ts"][label] = latest
            if out:
                new_files.append(str(out))
            msgs.append(f"slack {label}: {out.name if out else 'no new'}")
        except Exception as e:
            msgs.append(f"slack {label} FAILED: {e}")
            traceback.print_exc()
    return msgs, new_files


SECOND_BRAIN = ROOT.parent.parent  # Utils/.. = second-brain


def _find_claude() -> str:
    """Return the full path to the claude binary, checking common install locations."""
    import shutil
    # shutil.which uses the current PATH — may be narrow under cron
    found = shutil.which("claude")
    if found:
        return found
    # Common install locations not always on cron PATH
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "claude CLI not found. Add its directory to PATH in install_cron.sh, "
        "or set CLAUDE_BIN in .env."
    )


def run_wiki_update(state: dict, new_files: list[str], dry: bool) -> str:
    """Invoke `claude -p` to refresh the wiki against newly added raw files."""
    if not new_files:
        return "wiki: no new raw files — skipping update"
    if dry:
        return f"[dry] wiki: would update for {len(new_files)} new files"

    try:
        claude_bin = _find_claude()
    except FileNotFoundError as e:
        return f"wiki FAILED: {e}"

    file_list = "\n".join(f"- {f}" for f in new_files)
    prompt = (
        f"You are maintaining the second-brain wiki at {SECOND_BRAIN}/wiki/.\n"
        f"Read CLAUDE.md in {SECOND_BRAIN} for the schema and rules.\n\n"
        f"NEW raw files just added today:\n{file_list}\n\n"
        f"Tasks:\n"
        f"1. Read each new file under {SECOND_BRAIN}/raw/\n"
        f"2. Update or create relevant wiki articles in {SECOND_BRAIN}/wiki/\n"
        f"3. Maintain {SECOND_BRAIN}/wiki/INDEX.md\n"
        f"4. Use [[topic-name]] links between related articles\n"
        f"5. Never modify files under raw/\n\n"
        f"Be concise. Only update articles where the new content adds genuine signal."
    )

    result = subprocess.run(
        [claude_bin, "-p", prompt, "--permission-mode", "acceptEdits"],
        cwd=str(SECOND_BRAIN),
        capture_output=True, text=True,
        timeout=600,
    )
    if result.returncode != 0:
        return f"wiki FAILED: {result.stderr.strip()[:300]}"
    out_lines = result.stdout.strip().splitlines()
    return f"wiki: {out_lines[-1] if out_lines else 'updated'}"


# Exit code emitted by zoom_web_sync.py when interactive SSO re-login is needed.
ZOOM_EXIT_NEEDS_REAUTH = 2


def notify_user(title: str, message: str):
    """Best-effort macOS desktop notification. Silently no-op on other platforms."""
    try:
        safe_msg = message.replace('"', "'").replace("\\", "")
        safe_title = title.replace('"', "'").replace("\\", "")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}" sound name "Submarine"'],
            check=False, timeout=10,
        )
    except Exception:
        pass


def run_zoom(state: dict, dry: bool):
    new_files = []
    if dry:
        return f"[dry] zoom: incremental (existing files skipped)", new_files

    zoom_dir = SECOND_BRAIN / "raw" / "meetings"
    before = {p.name for p in zoom_dir.glob("*.md")} if zoom_dir.exists() else set()
    state["zoom_last_run"] = datetime.now().isoformat()

    zoom_script = ROOT.parent / "zoom-sync" / "zoom_web_sync.py"
    result = subprocess.run(
        [sys.executable, str(zoom_script), "--days", "30", "--headless"],
        capture_output=True, text=True,
    )

    if result.returncode == ZOOM_EXIT_NEEDS_REAUTH:
        notify_user(
            "Zoom ingestion needs re-auth",
            "Run: python zoom_web_sync.py --login",
        )
        return (
            f"zoom: NEEDS RE-AUTH — run `python zoom_web_sync.py --login` "
            f"({result.stderr.strip()[:200]})",
            new_files,
        )

    if result.returncode != 0:
        error_log = LOG_DIR / f"zoom_error_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
        error_log.write_text(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")
        hint = "timeout" if ("TimeoutError" in result.stderr or "timeout" in result.stderr.lower()) else "error"
        return (
            f"zoom FAILED ({hint}, see {error_log.name}): "
            f"{result.stderr.strip()[:200]}",
            new_files,
        )

    after = {p.name for p in zoom_dir.glob("*.md")}
    new_files = [str(zoom_dir / n) for n in (after - before)]

    suspiciously_small = []
    for fname in (after - before):
        fpath = zoom_dir / fname
        if fpath.stat().st_size < 500:
            suspiciously_small.append(f"{fname} ({fpath.stat().st_size}B)")

    out = result.stdout.strip().splitlines()
    summary = next((l for l in reversed(out) if "saved" in l or "Done" in l), out[-1] if out else "ok")

    if suspiciously_small:
        return (
            f"zoom: {summary} [WARNING: {len(suspiciously_small)} small files: "
            f"{', '.join(suspiciously_small[:3])}]",
            new_files,
        )
    return f"zoom: {summary}", new_files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state = load_state()
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
    log_lines = [f"=== Ingestion run {datetime.now().isoformat()} ==="]

    def log(msg):
        print(msg)
        log_lines.append(msg)

    new_files = []

    # 1. URLs from source file
    urls = parse_source_urls()
    log(f"\n--- URLs ({len(urls)}) ---")
    for url in urls:
        if url in state["urls_done"]:
            log(f"  skip (done): {url}")
            continue
        ok, msg, path = run_url(url, args.dry_run)
        log(f"  {msg}")
        if ok and not args.dry_run:
            state["urls_done"].append(url)
            if path:
                new_files.append(path)

    # 2. Slack incremental
    log(f"\n--- Slack ({len(SLACK_CHANNELS)} channels) ---")
    slack_msgs, slack_files = run_slack(state, args.dry_run)
    for line in slack_msgs:
        log(f"  {line}")
    new_files.extend(slack_files)

    # 3. Zoom incremental
    log(f"\n--- Zoom ---")
    zoom_msg, zoom_files = run_zoom(state, args.dry_run)
    log(f"  {zoom_msg}")
    new_files.extend(zoom_files)

    # 4. Refresh wiki against newly added raw files
    log(f"\n--- Wiki update ---")
    log(f"  {run_wiki_update(state, new_files, args.dry_run)}")

    if not args.dry_run:
        save_state(state)
    log_path.write_text("\n".join(log_lines) + "\n")
    log(f"\nLog → {log_path}")


if __name__ == "__main__":
    main()
