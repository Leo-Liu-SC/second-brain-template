#!/usr/bin/env python3
"""
Sync Zoom meeting notes from Zoom Notes (AI-generated, in-meeting notes)
via browser session (SSO login).

Usage as a module (from scheduler):
    from sources.zoom import fetch
    summary, new_files = fetch(days=30)

Usage as a CLI:
    python3 sources/zoom.py --login
    python3 sources/zoom.py [--days 30] [--headless] [--transcripts]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

SESSION_FILE = Path.home() / ".zoom_browser_session"
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "meetings"

HUB_HOME_URL = "https://hub.zoom.us/recent"
DOCS_API_BASE = "https://us01docs.zoom.us/api"

EXIT_NEEDS_REAUTH = 2
MIN_CONTENT_CHARS = 200
# Backstop for paginated listings: 200 pages x 50 items, far beyond real use.
MAX_LIST_PAGES = 200


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


def _hub_fetch(page, jwt: str, url: str, container: str = "drive/browser") -> dict | str:
    js = f"""
    async () => {{
        try {{
            const r = await fetch({json.dumps(url)}, {{
                credentials: 'include',
                headers: {{
                    'Authorization': 'Bearer ' + {json.dumps(jwt)},
                    'Accept': 'application/json, text/plain, */*',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-Zm-Docs-Container': {json.dumps(container)},
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


def _list_folder_children(page, jwt: str, account_id: str, parent_id: str, limit: int = 50) -> Iterator[dict]:
    """Yield every child of a Hub folder, newest-modified first.

    The endpoint returns at most `limit` children per call plus a
    `nextPageToken`, which goes back in the request body as `pageToken`.
    Pages are fetched lazily, so a caller that stops iterating early
    (e.g. once it is past its date cutoff) doesn't fetch the rest.
    """
    page_token = None
    seen_tokens = set()
    for _ in range(MAX_LIST_PAGES):
        body = {
            "parentId": parent_id,
            "accountId": account_id,
            "limit": limit,
            "sortBy": "lastModifiedTime",
            "desc": True,
            "fileFilters": [],
            "foldersMixedWithFiles": True,
            "flatten": False,
        }
        if page_token:
            body["pageToken"] = page_token
        result = _hub_fetch_post(page, jwt, f"{DOCS_API_BASE}/hub/files/action/batch_get_children_by_limit", body)
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(
                f"Failed to list folder {parent_id}: {result['error']} — body: {result.get('body','')}"
            )
        children = result.get("children") or []
        yield from children
        page_token = result.get("nextPageToken")
        if not children or not page_token:
            return
        if page_token in seen_tokens:
            print(f"  WARN: pagination token for folder {parent_id} stopped advancing — listing may be incomplete.")
            return
        seen_tokens.add(page_token)
    print(f"  WARN: folder {parent_id} still had more pages after {MAX_LIST_PAGES} — listing truncated.")


def _list_recent_files(page, jwt: str, limit: int = 50) -> Iterator[dict]:
    """Yield every entry of the user's Recent list, most recently operated first.

    `/file/recent` pages with a `nextPagingToken` in the response, sent back
    as the `pagingToken` query parameter. Fetched lazily, like
    `_list_folder_children`.
    """
    paging_token = None
    seen_tokens = set()
    for _ in range(MAX_LIST_PAGES):
        url = f"{DOCS_API_BASE}/file/recent?limit={limit}"
        if paging_token:
            url += f"&pagingToken={quote(paging_token, safe='')}"
        result = _hub_fetch(page, jwt, url)
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(
                f"Failed to list Zoom Hub notes: {result['error']} — body: {result.get('body','')}"
            )
        entries = result.get("recentFiles") or []
        yield from entries
        paging_token = result.get("nextPagingToken")
        if not entries or not paging_token:
            return
        if paging_token in seen_tokens:
            print("  WARN: pagination token for recent files stopped advancing — listing may be incomplete.")
            return
        seen_tokens.add(paging_token)
    print(f"  WARN: recent files still had more pages after {MAX_LIST_PAGES} — listing truncated.")


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
        # Children arrive newest-modified first (updatedInfo.time is the
        # lastModifiedTime sort key), and a doc can't be modified before it
        # was created, so once one was last modified before the cutoff every
        # remaining doc was also created before it.
        updated_time = (f.get("updatedInfo") or {}).get("time", "")
        if updated_time and updated_time < cutoff:
            break
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
    notes = []
    for entry in _list_recent_files(page, jwt):
        # Entries arrive most recently operated first, and a doc can't be
        # operated on before it exists, so once one was last operated before
        # the cutoff every remaining doc was also created before it.
        operated_time = entry.get("lastOperatedTime") or ""
        if operated_time and operated_time < cutoff:
            break
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
            "created_time": created_time,
            "meeting_id": (f.get("meetingNotes") or {}).get("meetingId", ""),
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


UNKNOWN_SPEAKER = "Unknown speaker"
# A notes doc appears when its meeting starts, and AI Companion keeps writing
# the transcript until shortly after it ends. Notes docs younger than this are
# left for a later run so a running meeting isn't saved half-transcribed. A
# heuristic: a meeting longer than this can still be saved incomplete.
TRANSCRIPT_SETTLE_HOURS = 3


def _speaker_labels(transcript: dict) -> dict[str, tuple[str, bool]]:
    """Map each transcript userId to (display label, is_real_name).

    Precedence per entry: `isUnknown` -> "Unknown speaker" (the username there
    is just the recording device's owner); else `speakerName`, the diarised
    label ("Speaker 2") when several people share one device; else `username`,
    the participant's display name (meeting-room devices show the room name).
    Only a `username` label counts as a real name.
    """
    labels = {}
    for sp in transcript.get("speakers") or []:
        if not isinstance(sp, dict) or not sp.get("userId"):
            continue
        user_id = str(sp["userId"])
        if sp.get("isUnknown"):
            labels[user_id] = (UNKNOWN_SPEAKER, False)
            continue
        diarised = (sp.get("speakerName") or "").strip()
        username = (sp.get("username") or "").strip()
        if diarised:
            labels[user_id] = (diarised, False)
        elif username:
            labels[user_id] = (username, True)
    return labels


def format_transcript(transcript: dict) -> str:
    """Render transcript fragments as speaker turns, in the same
    `### HH:MM:SS — Speaker` shape the zoom-ingest skill writes.

    Zoom returns one item per few seconds of speech; adjacent items from the
    same speaker are merged into one turn. Someone who rejoins gets a new
    userId under the same name, so real names are merged across userIds
    (two different people with the same display name speaking back to back
    merge too — the label couldn't tell them apart anyway). Unknown, diarised
    and unnamed speakers stay keyed by userId so distinct voices never merge.
    """
    labels = _speaker_labels(transcript)
    turns = []  # [key, label, start, [texts]]
    for item in transcript.get("items") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        user_id = str(item.get("userId") or "")
        if user_id:
            label, real_name = labels.get(user_id, (f"Speaker {user_id}", False))
        else:
            label, real_name = UNKNOWN_SPEAKER, False
        key = f"name:{label}" if real_name else f"id:{user_id or len(turns)}"
        if turns and turns[-1][0] == key:
            turns[-1][3].append(text)
        else:
            turns.append([key, label, str(item.get("startTime") or "?").split(".", 1)[0], [text]])

    if not turns:
        return ""
    return "## Transcript\n\n" + "\n\n".join(
        f"### {start} — {label}\n\n{' '.join(texts)}" for _, label, start, texts in turns
    )


def fetch_transcript(page, jwt: str, note: dict) -> str | None:
    """Return the rendered AI Companion transcript for a My Notes doc.

    Returns "" when the meeting has no accessible transcript, None when a
    request failed. The transcript hangs off the notes doc's own
    `meetingNotes.meetingId`; a meeting summary doc's owning meeting has a
    different meetingId whose transcript comes back empty, so summaries are
    not used here.
    """
    mid = quote(note["meeting_id"], safe="")

    status = _hub_fetch(page, jwt, f"{DOCS_API_BASE}/meeting/transcript_status?meetingId={mid}",
                        container="docs/browser")
    if not isinstance(status, dict) or "error" in status:
        print(f"  WARN: transcript status failed for {note['title']}: {status.get('error') if isinstance(status, dict) else status}")
        return None
    aic = status.get("aicTranscript") or {}
    if not (aic.get("exist") and status.get("canAccessAicTranscript")):
        return ""

    result = _hub_fetch(
        page, jwt,
        f"{DOCS_API_BASE}/bridge/meeting/transcripts/v2?meetingId={mid}&fileId={quote(note['id'], safe='')}",
        container="docs/browser",
    )
    if not isinstance(result, dict) or "error" in result:
        print(f"  WARN: transcript fetch failed for {note['title']}: {result.get('error') if isinstance(result, dict) else result}")
        return None
    return format_transcript(result)


def _save_transcripts(page, jwt: str, notes: list[dict]) -> tuple[int, int, int]:
    """Write `<note>-transcript.md` for each meeting notes doc. Returns (saved, deferred, failed)."""
    saved = deferred = failed = 0
    settle_cutoff = (datetime.now(timezone.utc) - timedelta(hours=TRANSCRIPT_SETTLE_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    for note in notes:
        if not note.get("meeting_id"):
            continue  # not a meeting notes doc
        filename = OUTPUT_DIR / f"{note['created_at']}_{slugify(note['title'])}-transcript.md"
        if filename.exists():
            continue
        if note.get("created_time", "") > settle_cutoff:
            print(f"  Transcript deferred (meeting may still be in progress): {note['title']}")
            deferred += 1
            continue
        try:
            content = fetch_transcript(page, jwt, note)
        except (PWError, PWTimeout, ValueError, TypeError, AttributeError) as e:
            print(f"  WARN: transcript failed for {note['title']}: {e}")
            content = None
        if content is None:
            failed += 1
            continue
        if not content:
            print(f"  No transcript available: {note['title']}")
            continue
        filename.write_text(format_note(note["title"], note["created_at"], content, source="zoom-transcript"))
        print(f"  Saved transcript ({filename.stat().st_size} bytes): {filename.name}")
        saved += 1
    return saved, deferred, failed


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
          on_reject=None, transcripts: bool = False) -> tuple[str, list[str]]:
    """Run the Zoom sync and return (summary_str, list_of_new_file_paths).

    on_reject, if given, is called with a one-line reason for every note that
    was fetched but not written, so a caller can route it into its own log.

    transcripts=True also writes each My Notes doc's verbatim AI Companion
    transcript, with speaker names, to `<note>-transcript.md`.

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

            t_saved = t_deferred = t_failed = 0
            if transcripts:
                print("Fetching transcripts...")
                t_saved, t_deferred, t_failed = _save_transcripts(page, jwt, notes)

        finally:
            browser.close()

    after = {p.name for p in OUTPUT_DIR.glob("*.md")}
    new_files = [str(OUTPUT_DIR / n) for n in (after - before)]

    suspiciously_small = []
    for fname in (after - before):
        fpath = OUTPUT_DIR / fname
        if fpath.stat().st_size < 500 and not (transcripts and fname.endswith("-transcript.md")):
            suspiciously_small.append(f"{fname} ({fpath.stat().st_size}B)")

    summary = f"Done. {saved} saved, {skipped} already existed."
    if transcripts:
        summary += f" {t_saved} transcripts saved."
        if t_deferred:
            summary += f" {t_deferred} deferred (meeting too recent)."
        if t_failed:
            summary += f" [WARNING: {t_failed} transcript fetches failed]"
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
    parser.add_argument("--transcripts", action="store_true",
                        help="Also save verbatim AI Companion transcripts with speaker names")
    args = parser.parse_args()

    try:
        summary, _ = fetch(days=args.days, login=args.login, headless=args.headless,
                           transcripts=args.transcripts)
        print(f"\n{summary}")
        sys.exit(0)
    except NeedsReauth as e:
        print(f"\nNEEDS_REAUTH: {e}", file=sys.stderr)
        sys.exit(EXIT_NEEDS_REAUTH)


if __name__ == "__main__":
    main()
