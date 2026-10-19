#!/usr/bin/env python3
"""
Slack read/send via browser SSO — no API/bot token.

    python3 slack_fetch.py doctor [--fix]                    # self-check / install deps
    python3 slack_fetch.py login                             # one-time SSO (visible browser)
    python3 slack_fetch.py read channel <name|link> [--today|--week|--month] [--json]
    python3 slack_fetch.py read dms|apps|groups [--today|--week|--month] [--json]
    python3 slack_fetch.py read dm <person> [--today|--week|--month] [--json]
    python3 slack_fetch.py send dm|channel|thread <target> "<text>"

Login opens a visible browser once; everything else runs headless and reuses the
saved session in ~/.second-brain/slack-ingest/. Uses its own Playwright Chromium —
your normal Chrome does not need to be open.
"""
import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Portable config/session live under ~/.second-brain so they migrate with the user's
# data bundle; the script/code lives with the skill folder.
CONFIG_DIR = Path.home() / ".second-brain" / "slack-ingest"
for _env in (CONFIG_DIR / ".env", Path(__file__).parent / ".env"):
    if _env.exists():
        load_dotenv(_env)
        break

SESSION_FILE = CONFIG_DIR / "session.json"
OUTPUT_DIR = CONFIG_DIR / "output"
DEFAULT_WORKSPACE_URL = os.environ.get("SLACK_WORKSPACE_URL", "")


# ── helpers ────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:60]


def _workspace_url() -> str:
    if DEFAULT_WORKSPACE_URL:
        return DEFAULT_WORKSPACE_URL
    print("ERROR: SLACK_WORKSPACE_URL is not set.")
    print("  Set it in .env:")
    print("  SLACK_WORKSPACE_URL=https://app.slack.com/client/TXXXXXXXX/CXXXXXXXX")
    sys.exit(1)


# ── login ──────────────────────────────────────────────────────────────────

def do_login(page):
    print("Opening Slack... Please complete SSO login in the browser window.")
    page.goto(_workspace_url())
    print("Waiting up to 5 minutes for login...")
    for _ in range(60):
        page.wait_for_timeout(5000)
        if "app.slack.com/client/T" in page.url:
            print(f"  Logged in: {page.url}")
            page.wait_for_timeout(3000)
            return
    raise RuntimeError("Login timed out after 5 minutes.")


def do_login_flow():
    """Open a visible browser for SSO and save the session."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        do_login(page)
        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved → {SESSION_FILE}", file=sys.stderr)
        browser.close()


def _session_days_left():
    """Days until the session 'd' cookie expires, or None if unknown/unreadable."""
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.load(open(SESSION_FILE))
        dc = next((c for c in data.get("cookies", [])
                   if c.get("name") == "d" and "slack.com" in c.get("domain", "")), None)
        if dc and dc.get("expires", -1) > 0:
            return (datetime.fromtimestamp(dc["expires"]) - datetime.now()).days
    except Exception:
        return None
    return None


def preflight(auto_login: bool = False):
    """Run before any read/send: warn when the session is near expiry, and handle
    a missing/expired session (auto-login when interactive or --auto-login, else exit 2)."""
    interactive = sys.stdin.isatty()
    missing = not SESSION_FILE.exists()
    days = _session_days_left()

    if missing or (days is not None and days < 0):
        why = "missing" if missing else "expired"
        if auto_login or interactive:
            print(f"⚠ Session {why} — launching SSO login (complete it in the browser)...", file=sys.stderr)
            do_login_flow()
        else:
            print(f"SESSION_EXPIRED ({why}): run  python3 {Path(__file__).name} --login", file=sys.stderr)
            sys.exit(2)
        return

    if days is not None and days <= 3:
        print(f"⚠ Session expires in ~{days} day(s). Consider refreshing soon: "
              f"python3 {Path(__file__).name} --login", file=sys.stderr)


# ── channel / workspace parsing ────────────────────────────────────────────

def _parse_channel_arg(channel: str) -> tuple[str, str]:
    """Return (workspace_id, channel_id) from a URL, T.../C..., or bare channel ID."""
    m = re.search(r"app\.slack\.com/client/(T[A-Z0-9]+)/([CDG][A-Z0-9]+)", channel)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"(T[A-Z0-9]+)/([CDG][A-Z0-9]+)", channel)
    if m:
        return m.group(1), m.group(2)
    if channel and channel[0] in "CDG":
        ws = _workspace_url()
        m = re.search(r"app\.slack\.com/client/(T[A-Z0-9]+)", ws)
        team_id = m.group(1) if m else ""
        return team_id, channel
    raise ValueError(
        f"Cannot parse channel: {channel!r}\n"
        "  Expected: https://app.slack.com/client/TXXXXXXXX/CXXXXXXXX"
    )


def _home_channel() -> tuple[str, str]:
    ws = _workspace_url()
    m = re.search(r"client/(T[A-Z0-9]+)/([CDG][A-Z0-9]+)", ws)
    if not m:
        raise ValueError("SLACK_WORKSPACE_URL must include a /T.../C... channel path")
    return m.group(1), m.group(2)


# ── token capture ──────────────────────────────────────────────────────────

def _capture_token(page, channel_id: str = None) -> tuple[str, str, str]:
    """Load the workspace client and read the xoxc token straight from localStorage.

    More robust than intercepting a request (which is flaky on a headless cold start).
    All API calls then run same-origin against app.slack.com/api/, with the session
    cookie supplied automatically by the Playwright context.
    """
    team_id, home = _home_channel()
    page.goto(f"https://app.slack.com/client/{team_id}/{home}", wait_until="domcontentloaded")

    token = None
    for _ in range(25):
        token = page.evaluate(
            """() => {
                try {
                    const cfg = JSON.parse(localStorage.localConfig_v2);
                    const tid = cfg.lastActiveTeamId || Object.keys(cfg.teams)[0];
                    return (cfg.teams[tid] && cfg.teams[tid].token) || null;
                } catch (e) { return null; }
            }"""
        )
        if token and token.startswith("xoxc-"):
            break
        page.wait_for_timeout(1000)

    if not token or not token.startswith("xoxc-"):
        raise RuntimeError(
            "Failed to read Slack token from localStorage.\n"
            "  Your saved session may have expired — run with --login to refresh."
        )
    return "app.slack.com", token, "/api/conversations.history"


# ── in-browser API calls ───────────────────────────────────────────────────

_RETRY_JS = """
async function sf(url, body) {
  for (let a = 0; a < 6; a++) {
    let r;
    try { r = await fetch(url, {method:'POST', body, credentials:'include'}); }
    catch (e) { await new Promise(s => setTimeout(s, 1500)); continue; }
    if (r.status === 429) {
      const ra = parseInt(r.headers.get('retry-after') || '3', 10);
      await new Promise(s => setTimeout(s, (ra + 1) * 1000)); continue;
    }
    const j = await r.json();
    if (!j.ok && j.error === 'ratelimited') { await new Promise(s => setTimeout(s, 3000)); continue; }
    return j;
  }
  return {ok:false, error:'ratelimited'};
}
"""


def _api_eval(page, host, body_js):
    """Run an async in-page fetch helper; a retrying `sf(url, body)` is available to body_js."""
    return page.evaluate("async () => {%s\n%s}" % (_RETRY_JS, body_js))


def _auth_test(page, host, token) -> str:
    js = f"""
        const f = new FormData(); f.append('token', {json.dumps(token)});
        const r = await fetch('https://{host}/api/auth.test', {{method:'POST', body:f, credentials:'include'}});
        const d = await r.json(); return d.user_id || '';
    """
    return _api_eval(page, host, js)


def _fetch_history(page, host, token, query, channel_id, oldest, limit=200):
    js = f"""
        const messages = [];
        let cursor = '';
        for (let i = 0; i < 20; i++) {{
            const form = new FormData();
            form.append('token', {json.dumps(token)});
            form.append('channel', {json.dumps(channel_id)});
            form.append('limit', '{limit}');
            form.append('oldest', '{oldest}');
            if (cursor) form.append('cursor', cursor);
            const data = await sf('https://{host}{query}', form);
            if (!data.ok) return {{error: data.error || 'unknown', got: messages.length}};
            messages.push(...(data.messages || []));
            if (!data.has_more) break;
            cursor = data.response_metadata?.next_cursor || '';
            if (!cursor) break;
        }}
        return {{messages}};
    """
    result = _api_eval(page, host, js)
    if "error" in result and result["error"]:
        raise RuntimeError(f"Slack API error: {result['error']}")
    return list(reversed(result["messages"]))


def _fetch_thread_replies(page, host, token, channel_id, thread_tss):
    if not thread_tss:
        return {}
    js = f"""
        const tss = {json.dumps(thread_tss)};
        const out = {{}};
        for (const ts of tss) {{
            try {{
                const form = new FormData();
                form.append('token', {json.dumps(token)});
                form.append('channel', {json.dumps(channel_id)});
                form.append('ts', ts);
                form.append('limit', '200');
                const d = await sf('https://{host}/api/conversations.replies', form);
                out[ts] = d.ok ? (d.messages || []).slice(1) : [];
            }} catch (e) {{ out[ts] = []; }}
        }}
        return out;
    """
    return _api_eval(page, host, js)


# ── markdown rendering ─────────────────────────────────────────────────────

# ── fetch ──────────────────────────────────────────────────────────────────

# ── read layer: shared session, permalink, user cache, collection ───────────

APP_NAMES = {"slackbot": "Slackbot", "jira": "Jira", "confluence": "Confluence",
             "rosanna": "Rosanna", "outlook": "Outlook"}
USER_CACHE = CONFIG_DIR / "users_cache.json"


@contextmanager
def _session(login: bool = False):
    """Open a (headless unless login) browser with the saved session and a captured token."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not login)
        ctx_kwargs = {"storage_state": str(SESSION_FILE)} if (SESSION_FILE.exists() and not login) else {}
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        if login or not SESSION_FILE.exists():
            do_login(page)
            context.storage_state(path=str(SESSION_FILE))
        host, token, query = _capture_token(page)
        domain = page.evaluate(
            """() => { try { const c = JSON.parse(localStorage.localConfig_v2);
                const t = c.lastActiveTeamId || Object.keys(c.teams)[0];
                return c.teams[t].domain || ''; } catch (e) { return ''; } }"""
        ) or "app"
        try:
            yield page, host, token, query, domain
        finally:
            browser.close()


def _permalink(domain, channel_id, ts):
    return f"https://{domain}.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


def _load_user_cache():
    try:
        return json.load(open(USER_CACHE))
    except Exception:
        return {}


def _resolve_users_full(page, host, token, ids):
    """Return {id: {name, is_bot}} for ids, backed by a disk cache."""
    ids = {i for i in ids if i}
    cache = _load_user_cache()
    missing = [i for i in ids if i not in cache]
    if missing:
        js = f"""
            const ids = {json.dumps(missing)};
            const out = {{}};
            for (const id of ids) {{
                const f = new FormData(); f.append('token', {json.dumps(token)}); f.append('user', id);
                try {{
                    const d = await sf('https://{host}/api/users.info', f);
                    out[id] = d.ok && d.user
                        ? {{name: (d.user.real_name || (d.user.profile && d.user.profile.real_name) || d.user.name || id), is_bot: !!d.user.is_bot}}
                        : {{name: id, is_bot: false}};
                }} catch (e) {{ out[id] = {{name: id, is_bot: false}}; }}
            }}
            return out;
        """
        fetched = _api_eval(page, host, js)
        cache.update(fetched)
        cache["USLACKBOT"] = {"name": "Slackbot", "is_bot": True}
        try:
            USER_CACHE.parent.mkdir(parents=True, exist_ok=True)
            json.dump(cache, open(USER_CACHE, "w"), ensure_ascii=False)
        except Exception:
            pass
    return {i: cache.get(i, {"name": i, "is_bot": False}) for i in ids}


def _fmt_text(text, names):
    t = text or ""
    t = re.sub(r"<@(U\w+|W\w+)>", lambda m: "@" + names.get(m.group(1), {}).get("name", m.group(1)), t)
    t = re.sub(r"<#(C\w+)\|([^>]*)>", lambda m: "#" + (m.group(2) or m.group(1)), t)
    t = re.sub(r"<!subteam\^\w+\|?([^>]*)>", lambda m: "@" + (m.group(1) or "group"), t)
    t = re.sub(r"<!(here|channel|everyone)>", lambda m: "@" + m.group(1), t)
    t = re.sub(r"<(https?:[^>|]+)\|([^>]*)>", lambda m: f"[{m.group(2)}]({m.group(1)})", t)
    t = re.sub(r"<(https?:[^>]+)>", lambda m: m.group(1), t)
    return t.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def _who(msg, names):
    if msg.get("user"):
        return names.get(msg["user"], {}).get("name", msg["user"]), msg["user"], names.get(msg["user"], {}).get("is_bot", False)
    nm = (msg.get("bot_profile") or {}).get("name") or msg.get("username") or "bot"
    return nm, msg.get("bot_id", ""), True


def _collect(page, host, token, query, domain, channel_id, oldest):
    """Fetch one conversation's messages (chronological) with threads + permalinks resolved."""
    messages = _fetch_history(page, host, token, query, channel_id, oldest)
    parents = [m["ts"] for m in messages if m.get("reply_count", 0) > 0]
    replies_map = _fetch_thread_replies(page, host, token, channel_id, parents) if parents else {}

    all_msgs = list(messages) + [r for rs in replies_map.values() for r in rs]
    ids = {m.get("user") for m in all_msgs if m.get("user")}
    for m in all_msgs:
        ids |= set(re.findall(r"<@(U\w+)>", m.get("text", "")))
    names = _resolve_users_full(page, host, token, ids)

    out = []
    for m in messages:
        author, aid, is_bot = _who(m, names)
        entry = {
            "ts": m["ts"], "author": author, "author_id": aid, "is_bot": is_bot,
            "permalink": _permalink(domain, channel_id, m["ts"]),
            "text": _fmt_text(m.get("text", ""), names).strip(),
        }
        replies = replies_map.get(m["ts"], [])
        if replies:
            entry["thread"] = [
                {"ts": r["ts"], **dict(zip(("author", "author_id", "is_bot"), _who(r, names))),
                 "text": _fmt_text(r.get("text", ""), names).strip()}
                for r in replies
            ]
        out.append(entry)
    return out


def _window(label):
    now = datetime.now()
    if label == "week":
        return (now - timedelta(days=7)).timestamp(), "past 7 days"
    if label == "month":
        return (now - timedelta(days=30)).timestamp(), "past 30 days"
    return datetime(now.year, now.month, now.day).timestamp(), "today"


def _convo(channel_id, name, ctype, label, messages):
    return {"channel": {"id": channel_id, "name": name, "type": ctype},
            "window": label, "count": len(messages), "messages": messages}


def _to_markdown(convo):
    c = convo["channel"]
    lines = [f"# {c['type']}: {c['name']}", f"_{convo['count']} messages · {convo['window']}_", ""]
    for m in convo["messages"]:
        bot = " [app]" if m["is_bot"] else ""
        lines.append(f"**[{datetime.fromtimestamp(float(m['ts'])):%Y-%m-%d %H:%M}] {m['author']}{bot}:** {m['text']}")
        lines.append(f"<{m['permalink']}>")
        for r in m.get("thread", []):
            rb = " [app]" if r["is_bot"] else ""
            lines.append(f"  > **[{datetime.fromtimestamp(float(r['ts'])):%Y-%m-%d %H:%M}] {r['author']}{rb}:** {r['text']}")
        lines.append("")
    return "\n".join(lines)


def _emit(payload, json_out, out_dir):
    convos = payload["conversations"] if "conversations" in payload else [payload]
    if json_out:
        print(json.dumps(payload, ensure_ascii=False))
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for convo in convos:
        if not convo["messages"]:
            print(f"  ({convo['channel']['name']}: no messages in window)", file=sys.stderr)
            continue
        md = _to_markdown(convo)
        path = out_dir / f"{datetime.now():%Y-%m-%d_%H%M}_{slugify(convo['channel']['name'])}.md"
        path.write_text(md + "\n")
        print(f"  {convo['channel']['name']}: {convo['count']} msgs → {path}", file=sys.stderr)


def _channel_name(page, host, token, cid):
    js = f"""
        const f = new FormData(); f.append('token', {json.dumps(token)}); f.append('channel', {json.dumps(cid)});
        const r = await fetch('https://{host}/api/conversations.info', {{method:'POST', body:f, credentials:'include'}});
        const d = await r.json(); return (d.ok && d.channel && d.channel.name) ? d.channel.name : '';
    """
    try:
        return _api_eval(page, host, js) or cid
    except Exception:
        return cid


def _resolve_channel(page, host, token, target):
    """(channel_id, name, type) from a URL, C/D/G id, or a channel name."""
    m = re.search(r"app\.slack\.com/client/T[A-Z0-9]+/([CDG][A-Z0-9]+)", target)
    if m:
        return m.group(1), _channel_name(page, host, token, m.group(1)), "channel"
    if target and target[0] in "CDG" and target[1:].replace("_", "").isalnum():
        return target, _channel_name(page, host, token, target), "channel"
    name = target.lstrip("#").strip().lower()
    cursor = ""
    # users.conversations = only the channels *you're in* — small and fast, unlike the
    # org-wide conversations.list.
    for _ in range(20):
        js = f"""
            const f = new FormData(); f.append('token', {json.dumps(token)});
            f.append('types', 'public_channel,private_channel'); f.append('exclude_archived', 'true');
            f.append('limit', '1000'); {("f.append('cursor', %s);" % json.dumps(cursor)) if cursor else ""}
            const r = await fetch('https://{host}/api/users.conversations', {{method:'POST', body:f, credentials:'include'}});
            return await r.json();
        """
        d = _api_eval(page, host, js)
        if not d.get("ok"):
            raise RuntimeError(f"users.conversations: {d.get('error')}")
        for ch in d.get("channels", []):
            if ch.get("name", "").lower() == name:
                return ch["id"], ch["name"], "channel"
        cursor = (d.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break
    raise RuntimeError(f"No channel named {target!r} found among channels you're in (try the link).")


def read_channel(target, win, json_out):
    oldest, label = _window(win)
    with _session() as (page, host, token, query, domain):
        cid, name, ctype = _resolve_channel(page, host, token, target)
        msgs = _collect(page, host, token, query, domain, cid, oldest)
    _emit(_convo(cid, name, ctype, label, msgs), json_out, OUTPUT_DIR)


def _list_ims(page, host, token):
    """List IM channels (id + partner id) — cheap: no per-DM history fetch."""
    js = f"""
        let ims = [], cursor = '';
        for (let i = 0; i < 5; i++) {{
            const f = new FormData(); f.append('token', {json.dumps(token)});
            f.append('types', 'im'); f.append('limit', '1000');
            if (cursor) f.append('cursor', cursor);
            const r = await fetch('https://{host}/api/conversations.list', {{method:'POST', body:f, credentials:'include'}});
            const d = await r.json();
            if (!d.ok) return {{error: d.error}};
            ims.push(...(d.channels || []).filter(c => !c.is_user_deleted).map(c => ({{id: c.id, partner: c.user}})));
            cursor = d.response_metadata?.next_cursor || '';
            if (!cursor) break;
        }}
        return {{ims}};
    """
    res = _api_eval(page, host, js)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(f"list ims: {res['error']}")
    return res.get("ims", [])


def _active_ids(page, host, token, kind, oldest):
    """Conversation ids of `kind` ('ims'|'mpims'|'channels') whose latest message is within
    the window. Uses client.counts (one call) as a cheap activity pre-filter — read state
    independent, so it never hides an already-read message."""
    js = f"""
        const f = new FormData(); f.append('token', {json.dumps(token)});
        const r = await fetch('https://{host}/api/client.counts', {{method:'POST', body:f, credentials:'include'}});
        const d = await r.json();
        if (!d.ok) return {{error: d.error}};
        return {{items: (d[{json.dumps(kind)}] || []).map(x => ({{id: x.id, latest: x.latest || '0',
                 mention_count: x.mention_count || 0, has_unreads: !!x.has_unreads}}))}};
    """
    res = _api_eval(page, host, js)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(f"client.counts: {res['error']}")
    return [it for it in res.get("items", []) if float(it.get("latest") or 0) >= oldest]


def _read_dm_set(win, json_out, want_apps, only_app=None):
    """`read dms` (humans) and `read apps`. Pre-filters DMs active in the window via
    client.counts, then collects only those (today/all states, threads included)."""
    oldest, label = _window(win)
    with _session() as (page, host, token, query, domain):
        active = {it["id"] for it in _active_ids(page, host, token, "ims", oldest)}
        if not active:
            _emit({"conversations": []}, json_out, OUTPUT_DIR)
            return
        partner = {im["id"]: im["partner"] for im in _list_ims(page, host, token) if im.get("partner")}
        ids = [(cid, partner[cid]) for cid in active if cid in partner]
        names = _resolve_users_full(page, host, token, {pid for _, pid in ids})

        convos = []
        for cid, pid in ids:
            info = names.get(pid, {"name": pid, "is_bot": False})
            is_app = info["is_bot"] or pid == "USLACKBOT"
            nm = info["name"].lower()
            if want_apps:
                if only_app:
                    if only_app not in nm and not (only_app == "slackbot" and pid == "USLACKBOT"):
                        continue
                elif not any(a in nm for a in APP_NAMES) and pid != "USLACKBOT":
                    continue
            elif is_app:
                continue
            msgs = _collect(page, host, token, query, domain, cid, oldest)
            if msgs:
                convos.append(_convo(cid, info["name"], "app" if is_app else "dm", label, msgs))
    _emit({"conversations": convos}, json_out, OUTPUT_DIR)


def _group_name(page, host, token, cid, my_id):
    """Human label for a group DM = its members' names (minus yourself)."""
    members = _api_eval(page, host, f"""
        const f = new FormData(); f.append('token', {json.dumps(token)});
        f.append('channel', {json.dumps(cid)}); f.append('limit', '100');
        const r = await fetch('https://{host}/api/conversations.members', {{method:'POST', body:f, credentials:'include'}});
        const d = await r.json(); return d.ok ? (d.members || []) : [];
    """)
    names = _resolve_users_full(page, host, token, set(members))
    others = [names[m]["name"] for m in members if m != my_id]
    return ", ".join(others) if others else cid


def read_groups(win, json_out):
    """Multi-person group DMs (mpim) active in the window. Group DMs have no fixed name —
    they're identified by members — so each is auto-discovered and labelled by its members."""
    oldest, label = _window(win)
    with _session() as (page, host, token, query, domain):
        my_id = _auth_test(page, host, token)
        active = _active_ids(page, host, token, "mpims", oldest)
        convos = []
        for it in active:
            cid = it["id"]
            msgs = _collect(page, host, token, query, domain, cid, oldest)
            if msgs:
                convos.append(_convo(cid, _group_name(page, host, token, cid, my_id), "group", label, msgs))
    _emit({"conversations": convos}, json_out, OUTPUT_DIR)


def read_dm(person, win, json_out):
    """The 1:1 DM with a person (matched by display name), within the window."""
    oldest, label = _window(win)
    with _session() as (page, host, token, query, domain):
        ims = _list_ims(page, host, token)
        names = _resolve_users_full(page, host, token, {im["partner"] for im in ims if im.get("partner")})
        target = person.strip().lower()
        match = next(((im["id"], names[im["partner"]]["name"]) for im in ims
                      if im.get("partner") in names and target in names[im["partner"]]["name"].lower()), None)
        if not match:
            print(f"No DM found with a partner matching {person!r}", file=sys.stderr)
            return
        cid, nm = match
        msgs = _collect(page, host, token, query, domain, cid, oldest)
    _emit(_convo(cid, nm, "dm", label, msgs), json_out, OUTPUT_DIR)


def read_dms(win, json_out):
    _read_dm_set(win, json_out, want_apps=False)


def read_apps(win, json_out, only_app=None):
    _read_dm_set(win, json_out, want_apps=True, only_app=(only_app.lower() if only_app else None))


def read_group(target, win, json_out):
    oldest, label = _window(win)
    with _session() as (page, host, token, query, domain):
        if not target:
            js = f"""
                const f = new FormData(); f.append('token', {json.dumps(token)});
                f.append('types', 'mpim'); f.append('limit', '200');
                const r = await fetch('https://{host}/api/conversations.list', {{method:'POST', body:f, credentials:'include'}});
                const d = await r.json(); return (d.channels || []).map(c => ({{id: c.id, name: c.name}}));
            """
            groups = _api_eval(page, host, js)
            print("Group DMs (use `read group <id>`):", file=sys.stderr)
            for g in groups:
                print(f"  {g['id']}  {g['name']}", file=sys.stderr)
            return
        cid = target if target[0] in "CDG" else target
        msgs = _collect(page, host, token, query, domain, cid, oldest)
    _emit(_convo(cid, cid, "group", label, msgs), json_out, OUTPUT_DIR)


# ── send layer ──────────────────────────────────────────────────────────────

def _parse_permalink(link):
    m = re.search(r"/archives/([CDG][A-Z0-9]+)/p(\d+)", link)
    if not m:
        raise ValueError(f"Not a Slack message link: {link!r}")
    digits = m.group(2)
    return m.group(1), digits[:-6] + "." + digits[-6:]


def _post(page, host, token, channel, text, thread_ts=None):
    extra = f"f.append('thread_ts', {json.dumps(thread_ts)});" if thread_ts else ""
    js = f"""
        const f = new FormData();
        f.append('token', {json.dumps(token)});
        f.append('channel', {json.dumps(channel)});
        f.append('text', {json.dumps(text)});
        {extra}
        return await sf('https://{host}/api/chat.postMessage', f);
    """
    d = _api_eval(page, host, js)
    if not d.get("ok"):
        raise RuntimeError(f"chat.postMessage: {d.get('error')}")
    return d


def _report_sent(domain, res, where):
    link = _permalink(domain, res.get("channel", ""), res.get("ts", ""))
    print(f"✓ sent to {where} (ts {res.get('ts')})", file=sys.stderr)
    print(link)  # stdout: where it landed


def send_dm(person, text):
    with _session() as (page, host, token, query, domain):
        ims = _list_ims(page, host, token)
        names = _resolve_users_full(page, host, token, {im["partner"] for im in ims if im.get("partner")})
        target = person.strip().lower()
        uid = next((im["partner"] for im in ims
                    if target in names.get(im["partner"], {}).get("name", "").lower()), None)
        if not uid:
            raise RuntimeError(f"No DM partner matching {person!r}")
        cid = _api_eval(page, host, f"""
            const f = new FormData(); f.append('token', {json.dumps(token)}); f.append('users', {json.dumps(uid)});
            const r = await fetch('https://{host}/api/conversations.open', {{method:'POST', body:f, credentials:'include'}});
            const d = await r.json(); return (d.ok && d.channel) ? d.channel.id : null;
        """)
        if not cid:
            raise RuntimeError("conversations.open failed")
        res = _post(page, host, token, cid, text)
        _report_sent(domain, res, names.get(uid, {}).get("name", uid))


def send_channel(target, text):
    with _session() as (page, host, token, query, domain):
        cid, name, _ = _resolve_channel(page, host, token, target)
        res = _post(page, host, token, cid, text)
        _report_sent(domain, res, f"#{name}")


def send_thread(link, text):
    cid, ts = _parse_permalink(link)
    with _session() as (page, host, token, query, domain):
        res = _post(page, host, token, cid, text, thread_ts=ts)
        _report_sent(domain, res, f"thread {ts}")


# ── doctor / self-check ────────────────────────────────────────────────────

def doctor(fix: bool = False) -> bool:
    """Check (and with --fix install) everything the skill needs. Returns True if ready."""
    import importlib.util
    import subprocess

    ok = True
    print(f"config dir : {CONFIG_DIR}")
    print(f"workspace  : {DEFAULT_WORKSPACE_URL or '(not set — add SLACK_WORKSPACE_URL to .env)'}")
    if not DEFAULT_WORKSPACE_URL:
        ok = False
    print()

    # 1. Python deps
    pip_missing = []
    for mod, pkg in (("playwright", "playwright"), ("dotenv", "python-dotenv")):
        if importlib.util.find_spec(mod) is None:
            pip_missing.append(pkg)
    if pip_missing:
        ok = False
        print(f"✗ Python deps missing: {', '.join(pip_missing)}")
        if fix:
            print("  installing...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pip_missing])
            print("  done.")
    else:
        print("✓ Python deps (playwright, python-dotenv)")

    # 2. Chromium
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    has_chromium = cache.exists() and any(cache.glob("chromium-*"))
    if not has_chromium:
        ok = False
        print("✗ Chromium not installed")
        if fix:
            print("  installing chromium (~150MB)...")
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
            print("  done.")
    else:
        print("✓ Chromium")

    # 3. Session
    if not SESSION_FILE.exists():
        ok = False
        print(f"✗ No saved session — run:  python3 {Path(__file__).name} --login")
    else:
        try:
            data = json.load(open(SESSION_FILE))
            dc = next((c for c in data.get("cookies", [])
                       if c.get("name") == "d" and "slack.com" in c.get("domain", "")), None)
            if dc and dc.get("expires", -1) > 0:
                exp = datetime.fromtimestamp(dc["expires"])
                days_left = (exp - datetime.now()).days
                if days_left < 0:
                    ok = False
                    print(f"✗ Session expired ({exp:%Y-%m-%d}) — run --login")
                else:
                    print(f"✓ Session present (valid until {exp:%Y-%m-%d}, ~{days_left}d; real life often shorter)")
            else:
                print("✓ Session present (expiry unknown)")
        except Exception as e:
            ok = False
            print(f"✗ Session file unreadable: {e}")

    print("\n" + ("READY ✓" if ok else
                  "NOT READY — run:  python3 %s doctor --fix   (and --login if session is missing)"
                  % Path(__file__).name))
    return ok


# ── CLI ────────────────────────────────────────────────────────────────────

def _win_from_args(a):
    if a.week:
        return "week"
    if a.month:
        return "month"
    return "today"


def main():
    p = argparse.ArgumentParser(prog="slack_fetch.py",
                                description="Slack read/ingest via browser SSO (no API token).")
    sub = p.add_subparsers(dest="cmd")

    pd = sub.add_parser("doctor", help="environment self-check (--fix to install)")
    pd.add_argument("--fix", action="store_true")
    sub.add_parser("login", help="open a visible browser, SSO, save the session")

    pr = sub.add_parser("read", help="read messages")
    pr.add_argument("what", choices=["channel", "dms", "apps", "groups", "group", "dm"])
    pr.add_argument("target", nargs="?", default=None, help="channel name/link, person name, or group id")
    pr.add_argument("--today", action="store_true", help="today only (default)")
    pr.add_argument("--week", action="store_true", help="past 7 days")
    pr.add_argument("--month", action="store_true", help="past 30 days")
    pr.add_argument("--app", default=None, help="with 'apps': slackbot|jira|confluence|rosanna|outlook")
    pr.add_argument("--json", action="store_true", help="emit structured JSON to stdout")
    pr.add_argument("--auto-login", action="store_true", help="auto-pop SSO if session missing/expired")

    ps = sub.add_parser("send", help="send a message (posts as you, with a bot signature)")
    ps.add_argument("what", choices=["dm", "channel", "thread"])
    ps.add_argument("target", help="person name | channel name/link | thread message link")
    ps.add_argument("text")
    ps.add_argument("--auto-login", action="store_true")

    args = p.parse_args()

    if args.cmd == "doctor":
        sys.exit(0 if doctor(fix=args.fix) else 2)
    if args.cmd == "login":
        do_login_flow()
        return
    if args.cmd == "send":
        preflight(auto_login=args.auto_login)
        if args.what == "dm":
            send_dm(args.target, args.text)
        elif args.what == "channel":
            send_channel(args.target, args.text)
        elif args.what == "thread":
            send_thread(args.target, args.text)
        return
    if args.cmd != "read":
        p.print_help()
        sys.exit(1)

    preflight(auto_login=args.auto_login)
    win = _win_from_args(args)
    what = args.what
    if what == "channel":
        if not args.target:
            p.error("read channel needs a channel name or link")
        read_channel(args.target, win, args.json)
    elif what == "dms":
        read_dms(win, args.json)
    elif what == "apps":
        read_apps(win, args.json, only_app=args.app)
    elif what == "groups":
        read_groups(win, args.json)
    elif what == "group":
        read_group(args.target, win, args.json)
    elif what == "dm":
        if not args.target:
            p.error("read dm needs a person name")
        read_dm(args.target, win, args.json)


if __name__ == "__main__":
    main()
