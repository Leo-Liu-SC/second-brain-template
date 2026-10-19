#!/usr/bin/env python3
"""
Sync Zoom meeting notes from Zoom Notes (AI-generated, in-meeting notes)
via browser session (SSO login).

Usage as a module (from scheduler):
    from sources.zoom import fetch
    summary, new_files = fetch(days=30)

Usage as a CLI:
    python3 sources/zoom.py --login
    python3 sources/zoom.py [--days 30] [--headless]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

SESSION_FILE = Path.home() / ".zoom_browser_session"
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "meetings"

HUB_HOME_URL = "https://hub.zoom.us/recent"
DOCS_API_BASE = "https://us01docs.zoom.us/api"

EXIT_NEEDS_REAUTH = 2
MIN_CONTENT_CHARS = 200


def slugify(text):
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:60]


def retry_with_backoff(func, max_retries=3, initial_delay=2):
    for attempt in range(max_retries):
        try:
            return func()
        except PWTimeout:
            if attempt == max_retries - 1:
                raise
            delay = initial_delay * (2 ** attempt)
            print(f"  Timeout on attempt {attempt + 1}/{max_retries}, retrying in {delay}s...")
            time.sleep(delay)
        except PWError as e:
            msg = str(e)
            if "ERR_ABORTED" in msg or "ERR_NETWORK_CHANGED" in msg or "frame was detached" in msg:
                if attempt == max_retries - 1:
                    raise
                delay = initial_delay * (2 ** attempt)
                print(f"  Navigation error on attempt {attempt + 1}/{max_retries} ({msg.splitlines()[0][:80]}), retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise


class NeedsReauth(RuntimeError):
    """Raised when interactive SSO is required but the run is headless or login failed."""


def do_login(page, headless=False):
    if headless:
        raise NeedsReauth(
            "Zoom session expired. Run interactively to re-authenticate:\n"
            "  python zoom.py --login"
        )
    print("Opening Zoom login page... Please complete SSO login in the browser.")
    page.goto(f"https://zoom.us/signin?continue={HUB_HOME_URL}&from=docs")
    try:
        page.get_by_text("SSO", exact=False).first.click(timeout=5000)
    except PWTimeout:
        pass
    print("Waiting for login (up to 5 minutes)...")
    try:
        page.wait_for_function(
            "() => location.hostname === 'hub.zoom.us' && !location.href.includes('signin')",
            timeout=300_000,
        )
    except PWTimeout:
        raise NeedsReauth(
            f"Zoom SSO did not complete within 5 minutes. Last URL: {page.url}\n"
            "Re-run interactively: python zoom.py --login"
        )
    print("Login successful.")


def _tolerant_goto(page, url: str):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    except PWError as e:
        msg = str(e)
        if "ERR_ABORTED" not in msg and "frame was detached" not in msg:
            raise
        page.wait_for_timeout(2000)


def _capture_docs_jwt(page, timeout_ms: int = 30_000) -> str:
    captured = {}

    def on_request(req):
        if "us01docs.zoom.us/api/" in req.url:
            auth = req.headers.get("authorization") or req.headers.get("Authorization")
            if auth and auth.lower().startswith("bearer "):
                captured.setdefault("jwt", auth[len("bearer "):])

    page.on("request", on_request)
    try:
        _tolerant_goto(page, HUB_HOME_URL)
    finally:
        pass

    waited = 0
    while waited < timeout_ms and "jwt" not in captured:
        page.wait_for_timeout(500)
        waited += 500

    page.remove_listener("request", on_request)
    if "jwt" not in captured:
        raise RuntimeError(
            "Failed to capture us01docs.zoom.us JWT — the Hub SPA never issued an "
            "authenticated API request. The session may be expired or Hub UI may have changed."
        )
    return captured["jwt"]


def _hub_fetch(page, jwt: str, url: str) -> dict | str:
    js = f"""
    async () => {{
        try {{
            const r = await fetch({json.dumps(url)}, {{
                credentials: 'include',
                headers: {{
                    'Authorization': 'Bearer ' + {json.dumps(jwt)},
                    'Accept': 'application/json, text/plain, */*',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-Zm-Docs-Container': 'drive/browser',
                    'X-Zm-Cluster-Id': 'aw1'
                }}
            }});
            const text = await r.text();
            let data = null;
            try {{ data = JSON.parse(text); }} catch (e) {{}}
            if (!r.ok) return {{error: 'http ' + r.status, body: text.slice(0, 500)}};
            return data ?? {{error: 'non-json', body: text.slice(0, 500)}};
        }} catch (e) {{
            return {{error: 'fetch-exception', body: e.name + ': ' + e.message}};
        }}
    }}
    """
    return page.evaluate(js)


def _jwt_account_id(jwt: str) -> str:
    payload_b64 = jwt.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    return payload["aid"]


def _hub_fetch_post(page, jwt: str, url: str, body: dict) -> dict | str:
    js = f"""
    async () => {{
        try {{
            const r = await fetch({json.dumps(url)}, {{
                method: 'POST',
                credentials: 'include',
                headers: {{
                    'Authorization': 'Bearer ' + {json.dumps(jwt)},
                    'Accept': 'application/json, text/plain, */*',
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-Zm-Docs-Container': 'drive/browser',
                    'X-Zm-Cluster-Id': 'aw1'
                }},
                body: {json.dumps(json.dumps(body))}
            }});
            const text = await r.text();
            let data = null;
            try {{ data = JSON.parse(text); }} catch (e) {{}}
            if (!r.ok) return {{error: 'http ' + r.status, body: text.slice(0, 500)}};
            return data ?? {{error: 'non-json', body: text.slice(0, 500)}};
        }} catch (e) {{
            return {{error: 'fetch-exception', body: e.name + ': ' + e.message}};
        }}
    }}
    """
    return page.evaluate(js)


def _list_folder_children(page, jwt: str, account_id: str, parent_id: str, limit: int = 50) -> list[dict]:
    result = _hub_fetch_post(page, jwt, f"{DOCS_API_BASE}/hub/files/action/batch_get_children_by_limit", {
        "parentId": parent_id,
        "accountId": account_id,
        "limit": limit,
        "sortBy": "lastModifiedTime",
        "desc": True,
        "fileFilters": [],
        "foldersMixedWithFiles": True,
        "flatten": False,
    })
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(
            f"Failed to list folder {parent_id}: {result['error']} — body: {result.get('body','')}"
        )
    return result.get("children", [])


def find_summary_folder_id(page, jwt: str, account_id: str) -> str | None:
    """Find the 'Meeting summaries' folder (sister of 'My notes') under My Files.

    Identified by the systemFolderExtension.isSummaryFolder flag rather than
    title, since the display name isn't guaranteed stable across locales/UI changes.
    """
    for child in _list_folder_children(page, jwt, account_id, "my-docs"):
        if (child.get("systemFolderExtension") or {}).get("isSummaryFolder"):
            return child.get("id")
    return None


def list_summaries(page, jwt: str, account_id: str, folder_id: str, days_back: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
    summaries = []
    for f in _list_folder_children(page, jwt, account_id, folder_id):
        if f.get("fileType") != "doc":
            continue
        created_time = (f.get("createdInfo") or {}).get("time", "")
        if created_time and created_time < cutoff:
            continue
        summaries.append({
            "id": f.get("id", ""),
            "title": f.get("title", "untitled"),
            "created_at": created_time[:10] if created_time else "unknown",
        })
    return summaries


def list_notes(page, jwt: str, days_back: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
    result = _hub_fetch(page, jwt, f"{DOCS_API_BASE}/file/recent?limit=50")
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(
            f"Failed to list Zoom Hub notes: {result['error']} — body: {result.get('body','')}"
        )

    notes = []
    for entry in result.get("recentFiles", []):
        f = entry.get("file") or {}
        if f.get("fileType") not in (None, "doc"):
            continue
        created_info = f.get("createdInfo") or {}
        created_time = created_info.get("time", "")
        if created_time and created_time < cutoff:
            continue
        notes.append({
            "id": f.get("id", ""),
            "title": f.get("title", "untitled"),
            "created_at": created_time[:10] if created_time else "unknown",
        })
    return notes


def fetch_note_content(page, jwt: str, note_id: str, attempts: int = 3, retry_wait_ms: int = 2500) -> str:
    """Fetch a note body, retrying while the result is short.

    Zoom intermittently serves a document containing only its title block, then
    the full body a moment later. Returns the last body read, short or not — the
    caller applies the MIN_CONTENT_CHARS floor.
    """
    url = f"{DOCS_API_BASE}/page/{note_id}/content?returnEncodedData=true&fileId={note_id}"
    text = ""
    for attempt in range(1, attempts + 1):
        result = _hub_fetch(page, jwt, url)
        if isinstance(result, dict) and "error" in result:
            print(f"  WARN: content fetch failed for {note_id}: {result['error']} {result.get('body','')[:200]}")
        else:
            text = _extract_text_from_zoom_doc(result)
            if len(text.strip()) >= MIN_CONTENT_CHARS:
                return text
        if attempt < attempts:
            print(f"  Short body ({len(text.strip())} chars) on attempt {attempt}/{attempts}, retrying in {retry_wait_ms / 1000:g}s...")
            page.wait_for_timeout(retry_wait_ms)
    return text


_BLOCK_PREFIX = {
    "BLOCK_TYPE_HEADING1":   "# ",
    "BLOCK_TYPE_HEADING2":   "## ",
    "BLOCK_TYPE_HEADING3":   "### ",
    "BLOCK_TYPE_BULLET":     "- ",
    "BLOCK_TYPE_NUMBER":     "1. ",
    "BLOCK_TYPE_TODO_LIST":  "- [ ] ",
    "BLOCK_TYPE_QUOTE":      "> ",
    "BLOCK_TYPE_PAGE":       "",
    "BLOCK_TYPE_TEXT":       "",
}


def _parse_zoom_inline(s: str) -> str:
    if not isinstance(s, str) or not s.strip():
        return ""
    try:
        spans = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s.strip()
    parts = []
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, list) and len(span) >= 2 and isinstance(span[1], str):
                parts.append(span[1])
            elif isinstance(span, str):
                parts.append(span)
    return "".join(parts).strip()


def _extract_text_from_zoom_doc(doc) -> str:
    if not isinstance(doc, dict):
        return ""

    raw = (doc.get("content") or {}).get("data")
    if not raw or not isinstance(raw, str):
        return ""

    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  WARN: could not decode content.data: {e}")
        return ""

    blocks = payload.get("blocks") or {}
    if not isinstance(blocks, dict):
        return ""

    items = sorted(blocks.values(), key=lambda b: b.get("seq", "") if isinstance(b, dict) else "")

    lines: list[str] = []
    for block in items:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        content = block.get("content") or {}
        title_raw = content.get("title", "")
        text = _parse_zoom_inline(title_raw)
        if not text:
            continue
        prefix = _BLOCK_PREFIX.get(btype, "")
        lines.append(f"{prefix}{text}")

    return "\n".join(lines).strip()


def format_note(title: str, created_at: str, content: str, source: str = "zoom") -> str:
    return (
        f"---\n"
        f"source: {source}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"title: {title}\n"
        f"date: {created_at}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{content}\n"
    )


def _save_items(page, jwt: str, items: list[dict], source: str, filename_suffix: str = "",
                on_reject=None) -> tuple[int, int, list[str]]:
    saved = skipped = 0
    rejected = []
    for item in items:
        date = item["created_at"]
        title = item["title"]
        filename = OUTPUT_DIR / f"{date}_{slugify(title)}{filename_suffix}.md"

        if filename.exists():
            existing_size = filename.stat().st_size
            if existing_size >= 500:
                print(f"  Skipped (exists, {existing_size} bytes): {title}")
                skipped += 1
                continue
            print(f"  Re-fetching (existing file is only {existing_size}B): {title}")
        else:
            print(f"  Fetching: {title}")
        content = fetch_note_content(page, jwt, item["id"])

        if not content or len(content.strip()) < MIN_CONTENT_CHARS:
            reason = (f"short content ({len(content.strip())} chars, "
                      f"need ≥{MIN_CONTENT_CHARS}) after 3 attempts: {title}")
            print(f"  WARNING: skipping — {reason}")
            rejected.append(reason)
            if on_reject:
                on_reject(reason)
            continue

        filename.write_text(format_note(title, date, content, source=source))
        print(f"  Saved ({filename.stat().st_size} bytes): {filename.name}")
        saved += 1
    return saved, skipped, rejected


def fetch(days: int = 30, login: bool = False, headless: bool = True,
          on_reject=None) -> tuple[str, list[str]]:
    """Run the Zoom sync and return (summary_str, list_of_new_file_paths).

    on_reject, if given, is called with a one-line reason for every note that
    was fetched but not written, so a caller can route it into its own log.

    Raises NeedsReauth if the session is expired and headless=True.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in OUTPUT_DIR.glob("*.md")}

    with sync_playwright() as pw:
        run_headless = headless and not login
        browser = pw.chromium.launch(headless=run_headless)
        ctx_kwargs = {}
        if SESSION_FILE.exists() and not login:
            ctx_kwargs["storage_state"] = str(SESSION_FILE)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            if login or not SESSION_FILE.exists():
                do_login(page, headless=run_headless)
                context.storage_state(path=str(SESSION_FILE))
                print(f"Session saved to {SESSION_FILE}")

            print("Validating session...")

            def validate_session():
                _tolerant_goto(page, HUB_HOME_URL)
                page.wait_for_timeout(2000)
                return page.url

            try:
                url = retry_with_backoff(validate_session, max_retries=3, initial_delay=2)
            except (PWTimeout, PWError):
                print("ERROR: Could not load Zoom Hub after 3 retries — likely a network issue.", file=sys.stderr)
                raise

            if "signin" in url or "sso" in url:
                print("Session expired. Re-running login...")
                do_login(page, headless=run_headless)
                context.storage_state(path=str(SESSION_FILE))
                _tolerant_goto(page, HUB_HOME_URL)
            print("Session valid.")

            print("Booting Hub SPA and capturing API token...")
            jwt = _capture_docs_jwt(page)
            print(f"Captured JWT (len={len(jwt)}).")

            print(f"Listing notes from the last {days} days...")
            notes = list_notes(page, jwt, days)
            print(f"Found {len(notes)} notes.")
            saved, skipped, rejected = _save_items(page, jwt, notes, source="zoom", on_reject=on_reject)

            account_id = _jwt_account_id(jwt)
            summary_folder_id = find_summary_folder_id(page, jwt, account_id)
            if summary_folder_id:
                print(f"Listing meeting summaries from the last {days} days...")
                summaries = list_summaries(page, jwt, account_id, summary_folder_id, days)
                print(f"Found {len(summaries)} summaries.")
                s_saved, s_skipped, s_rejected = _save_items(
                    page, jwt, summaries, source="zoom-summary", filename_suffix="-summary", on_reject=on_reject
                )
                saved += s_saved
                skipped += s_skipped
                rejected += s_rejected
            else:
                print("No Meeting summaries folder found — skipping.")

        finally:
            browser.close()

    after = {p.name for p in OUTPUT_DIR.glob("*.md")}
    new_files = [str(OUTPUT_DIR / n) for n in (after - before)]

    suspiciously_small = []
    for fname in (after - before):
        fpath = OUTPUT_DIR / fname
        if fpath.stat().st_size < 500:
            suspiciously_small.append(f"{fname} ({fpath.stat().st_size}B)")

    summary = f"Done. {saved} saved, {skipped} already existed."
    if rejected:
        summary += f" {len(rejected)} rejected (short content)."
    if suspiciously_small:
        summary += f" [WARNING: {len(suspiciously_small)} small files: {', '.join(suspiciously_small[:3])}]"

    return summary, new_files


def main():
    parser = argparse.ArgumentParser(description="Sync Zoom notes via browser session")
    parser.add_argument("--login", action="store_true", help="Force interactive SSO login")
    parser.add_argument("--days", type=int, default=30, help="Days back to fetch (default: 30)")
    parser.add_argument("--headless", action="store_true", help="Headless mode (after first login)")
    args = parser.parse_args()

    try:
        summary, _ = fetch(days=args.days, login=args.login, headless=args.headless)
        print(f"\n{summary}")
        sys.exit(0)
    except NeedsReauth as e:
        print(f"\nNEEDS_REAUTH: {e}", file=sys.stderr)
        sys.exit(EXIT_NEEDS_REAUTH)


if __name__ == "__main__":
    main()
