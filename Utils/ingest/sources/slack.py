"""Fetch Slack messages via browser session (SSO login).

Uses Playwright with a saved browser session. Calls Slack's internal
conversations.history API directly using the in-browser fetch (which
auto-includes cookies and the xoxc token).

Usage in ingest.py:
    python3 ingest.py slack --login                 # first time, SSO login
    python3 ingest.py slack <channel-url-or-id>     # fetch a channel
    python3 ingest.py slack <id> --days 7
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SESSION_FILE = Path.home() / ".slack_browser_session"
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "slack"

# Workspace URL — set SLACK_WORKSPACE_URL in .env (any channel in your workspace works)
# Format: https://app.slack.com/client/TXXXXXXXX/CXXXXXXXX
DEFAULT_WORKSPACE_URL = os.environ.get(
    "SLACK_WORKSPACE_URL",
    "https://app.slack.com/client/TXXXXXXXXX/CXXXXXXXXX",
)


def slugify(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:60]


def do_login(page):
    print("Opening Slack... Please complete SSO login.")
    page.goto(DEFAULT_WORKSPACE_URL)
    print("Waiting up to 5 minutes for login...")
    for _ in range(60):
        page.wait_for_timeout(5000)
        if "app.slack.com/client/T" in page.url:
            print(f"Logged in. URL: {page.url}")
            page.wait_for_timeout(3000)
            return
    raise RuntimeError("Login timed out.")


def _parse_channel_arg(channel: str) -> tuple[str, str]:
    """Return (workspace_id, channel_id) from a URL or 'T.../C...' or just 'C...'."""
    m = re.search(r"app\.slack\.com/client/(T[A-Z0-9]+)/([CDG][A-Z0-9]+)", channel)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"(T[A-Z0-9]+)/([CDG][A-Z0-9]+)", channel)
    if m:
        return m.group(1), m.group(2)
    if channel and channel[0] in "CDG":
        # Workspace defaults to whatever the saved session uses
        m = re.search(r"app\.slack\.com/client/(T[A-Z0-9]+)", DEFAULT_WORKSPACE_URL)
        team_id = m.group(1) if m else ""
        return team_id, channel
    raise ValueError(f"Cannot parse channel: {channel}")


def _capture_token(page, channel_id: str) -> tuple[str, str, str]:
    """Visit Slack and intercept the xoxc token, host, and query string."""
    captured = {}

    def on_request(req):
        if "conversations.history" in req.url and req.method == "POST":
            m = re.match(r"https://([^/]+)(/api/conversations\.history\?[^\s]+)", req.url)
            if m:
                captured["host"] = m.group(1)
                captured["query"] = m.group(2)
            data = req.post_data or ""
            tm = re.search(r'name="token"\s*\r?\n\r?\n(xoxc-[^\r\n]+)', data)
            if tm:
                captured["token"] = tm.group(1)

    page.on("request", on_request)

    team_id, _ = _parse_channel_arg(channel_id) if "/" in channel_id else ("", channel_id)
    if not team_id:
        m = re.search(r"app\.slack\.com/client/(T[A-Z0-9]+)", DEFAULT_WORKSPACE_URL)
        team_id = m.group(1) if m else ""

    # Visit the workspace home (any channel triggers conversations.history;
    # DMs use other APIs, so always go to a known channel first)
    home_match = re.search(r"client/T[A-Z0-9]+/([CDG][A-Z0-9]+)", DEFAULT_WORKSPACE_URL)
    home_channel = home_match.group(1) if home_match else channel_id
    page.goto(f"https://app.slack.com/client/{team_id}/{home_channel}", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    page.remove_listener("request", on_request)
    if "token" not in captured:
        raise RuntimeError("Failed to capture Slack xoxc token.")
    return captured["host"], captured["token"], captured.get("query", "/api/conversations.history")


def _fetch_history(page, host: str, token: str, query: str, channel_id: str, oldest: float, limit: int = 200) -> list:
    """Use the page's fetch (so cookies are sent) to call conversations.history."""
    js = f"""
    async () => {{
        const messages = [];
        let cursor = '';
        let firstResponse = null;
        for (let i = 0; i < 20; i++) {{
            const form = new FormData();
            form.append('token', {json.dumps(token)});
            form.append('channel', {json.dumps(channel_id)});
            form.append('limit', '{limit}');
            form.append('oldest', '{oldest}');
            if (cursor) form.append('cursor', cursor);
            const resp = await fetch('https://{host}{query}', {{
                method: 'POST',
                body: form,
                credentials: 'include',
            }});
            const data = await resp.json();
            if (i === 0) firstResponse = {{ok: data.ok, error: data.error, has_more: data.has_more, msg_count: (data.messages || []).length, keys: Object.keys(data)}};
            if (!data.ok) return {{error: data.error || 'unknown', got: messages.length, debug: firstResponse}};
            messages.push(...(data.messages || []));
            if (!data.has_more) break;
            cursor = data.response_metadata?.next_cursor || '';
            if (!cursor) break;
        }}
        return {{messages, debug: firstResponse}};
    }}
    """
    result = page.evaluate(js)
    if "error" in result and result["error"]:
        raise RuntimeError(f"Slack API error: {result['error']} (got {result.get('got', 0)} so far)")
    return list(reversed(result["messages"]))  # chronological


def _fetch_thread_replies(page, host: str, token: str, channel_id: str, thread_tss: list) -> dict:
    """Fetch replies for each parent message ts. Returns {parent_ts: [replies]}."""
    if not thread_tss:
        return {}
    js = f"""
    async () => {{
        const tss = {json.dumps(thread_tss)};
        const out = {{}};
        for (const ts of tss) {{
            try {{
                const form = new FormData();
                form.append('token', {json.dumps(token)});
                form.append('channel', {json.dumps(channel_id)});
                form.append('ts', ts);
                form.append('limit', '200');
                const resp = await fetch('https://{host}/api/conversations.replies', {{
                    method: 'POST', body: form, credentials: 'include',
                }});
                const d = await resp.json();
                if (d.ok) {{
                    // First message is the parent — skip it, keep replies
                    out[ts] = (d.messages || []).slice(1);
                }} else {{
                    out[ts] = [];
                }}
            }} catch (e) {{
                out[ts] = [];
            }}
        }}
        return out;
    }}
    """
    return page.evaluate(js)


def _resolve_users(page, host: str, token: str, user_ids: set) -> dict:
    """Resolve user IDs to display names via users.info."""
    if not user_ids:
        return {}
    js = f"""
    async () => {{
        const ids = {json.dumps(list(user_ids))};
        const out = {{}};
        for (const id of ids) {{
            const form = new FormData();
            form.append('token', {json.dumps(token)});
            form.append('user', id);
            try {{
                const resp = await fetch('https://{host}/api/users.info', {{
                    method: 'POST', body: form, credentials: 'include',
                }});
                const d = await resp.json();
                if (d.ok && d.user) {{
                    out[id] = d.user.real_name || d.user.profile?.real_name || d.user.name || id;
                }} else {{
                    out[id] = id;
                }}
            }} catch (e) {{
                out[id] = id;
            }}
        }}
        return out;
    }}
    """
    return page.evaluate(js)


def _resolve_channel_name(page, host: str, token: str, channel_id: str) -> str:
    js = f"""
    async () => {{
        try {{
            const form = new FormData();
            form.append('token', {json.dumps(token)});
            form.append('channel', {json.dumps(channel_id)});
            const resp = await fetch('https://{host}/api/conversations.info', {{
                method: 'POST', body: form, credentials: 'include',
            }});
            const d = await resp.json();
            if (!d.ok) return '';
            const ch = d.channel || {{}};
            if (ch.is_im) return 'dm-' + (ch.user || {json.dumps(channel_id)});
            if (ch.is_mpim) return ch.name || ('mpim-' + {json.dumps(channel_id)});
            return ch.name || '';
        }} catch (e) {{
            return 'fetch-error:' + e.message;
        }}
    }}
    """
    name = page.evaluate(js)
    if name and name.startswith("fetch-error:"):
        print(f"  [warn] channel info fetch failed: {name}")
        return channel_id
    if name and name.startswith("dm-U"):
        # Resolve the user ID
        uid = name[3:]
        user_map = _resolve_users(page, host, token, {uid})
        return f"dm-{user_map.get(uid, uid)}"
    return name or channel_id


def fetch(channel: str, days: int = 7, login: bool = False, oldest_ts: float = None):
    """Return (output_path, latest_ts). oldest_ts overrides days when given."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    team_id, channel_id = _parse_channel_arg(channel)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not login)
        ctx_kwargs = {}
        if SESSION_FILE.exists() and not login:
            ctx_kwargs["storage_state"] = str(SESSION_FILE)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        if login or not SESSION_FILE.exists():
            do_login(page)
            context.storage_state(path=str(SESSION_FILE))
            print(f"Session saved to {SESSION_FILE}")

        host, token, query = _capture_token(page, channel_id)
        print(f"Captured token from {host}")

        # Navigate to the workspace host (a non-redirecting page) so subsequent
        # fetches are same-origin. /customize stays on the host domain.
        page.goto(f"https://{host}/customize", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        if oldest_ts is not None:
            oldest = oldest_ts
            print(f"Fetching from ts={oldest:.0f} from #{channel_id}...")
        else:
            oldest = (datetime.now() - timedelta(days=days)).timestamp()
            print(f"Fetching last {days} days from #{channel_id}...")
        messages = _fetch_history(page, host, token, query, channel_id, oldest)
        print(f"Got {len(messages)} top-level messages")

        # Fetch thread replies for any message with reply_count > 0
        thread_parents = [m["ts"] for m in messages if m.get("reply_count", 0) > 0]
        replies_map = {}
        if thread_parents:
            print(f"Fetching replies for {len(thread_parents)} threads...")
            replies_map = _fetch_thread_replies(page, host, token, channel_id, thread_parents)
            total_replies = sum(len(r) for r in replies_map.values())
            print(f"Got {total_replies} thread replies")

        # Collect all user IDs: senders + mentions across messages AND replies
        all_msgs = list(messages) + [r for rs in replies_map.values() for r in rs]
        mention_ids = set()
        for m in all_msgs:
            mention_ids.update(re.findall(r"<@(U[A-Z0-9]+)>", m.get("text", "")))
        user_ids = {m.get("user") for m in all_msgs if m.get("user")}
        user_ids |= mention_ids
        user_map = _resolve_users(page, host, token, user_ids)
        user_map.setdefault("USLACKBOT", "Slackbot")

        # Derive channel name: for DMs, use the other user's name
        if channel_id.startswith("D"):
            sender_ids = {m.get("user") for m in all_msgs if m.get("user")} - {"USLACKBOT"}
            if sender_ids:
                partner = next(iter(sender_ids))
                channel_name = f"dm-{user_map.get(partner, partner)}"
            elif "USLACKBOT" in user_ids:
                channel_name = "dm-Slackbot"
            else:
                channel_name = f"dm-{channel_id}"
        else:
            channel_name = channel_id

        browser.close()

    def replace_mentions(text: str) -> str:
        return re.sub(r"<@(U[A-Z0-9]+)>", lambda m: f"@{user_map.get(m.group(1), m.group(1))}", text)

    # Format markdown
    lines = []
    for msg in messages:
        ts = datetime.fromtimestamp(float(msg["ts"]))
        user_id = msg.get("user", "") or msg.get("username", "bot")
        user = user_map.get(user_id, user_id)
        text = replace_mentions(msg.get("text", "").strip())
        lines.append(f"**[{ts.strftime('%Y-%m-%d %H:%M')}] {user}:** {text}")

        for reply in replies_map.get(msg.get("ts", ""), []):
            rts = datetime.fromtimestamp(float(reply["ts"]))
            ruid = reply.get("user", "") or reply.get("username", "bot")
            ruser = user_map.get(ruid, ruid)
            rtext = replace_mentions(reply.get("text", "").strip())
            lines.append(f"  > **[{rts.strftime('%Y-%m-%d %H:%M')}] {ruser}:** {rtext}")
        lines.append("")

    if not messages:
        print("  No new messages.")
        return None, oldest_ts

    # Latest message ts becomes the new watermark
    latest_ts = max(float(m["ts"]) for m in messages)

    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_name = slugify(channel_name)
    out_path = OUTPUT_DIR / f"{date_str}_{safe_name}.md"
    out_path.write_text(
        f"---\n"
        f"source: slack\n"
        f"channel: #{channel_name}\n"
        f"channel_id: {channel_id}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"oldest_ts: {oldest}\n"
        f"latest_ts: {latest_ts}\n"
        f"title: Slack #{channel_name}\n"
        f"---\n\n"
        f"# Slack #{channel_name}\n\n"
        f"_{len(messages)} messages_\n\n"
        + "\n".join(lines)
        + "\n"
    )
    return out_path, latest_ts
