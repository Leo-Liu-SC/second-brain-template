"""Fetch a Confluence page and save as Markdown."""
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from markdownify import markdownify

BASE_URL = os.environ.get("CONFLUENCE_BASE_URL", "")
EMAIL = os.environ.get("CONFLUENCE_EMAIL", "")
TOKEN = os.environ.get("CONFLUENCE_API_TOKEN", "")
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "confluence"


def _auth():
    return (EMAIL, TOKEN)


def _page_id_from_url(url: str) -> str:
    """Extract page ID from a full URL or short /wiki/x/ link."""
    # Short link: /wiki/x/<base62id>
    m = re.search(r"/wiki/x/([A-Za-z0-9_\-]+)", url)
    if m:
        return _resolve_tiny_link(m.group(1))
    # Numeric ID in path
    m = re.search(r"/pages/(\d+)", url)
    if m:
        return m.group(1)
    # Bare numeric ID
    if url.isdigit():
        return url
    raise ValueError(f"Cannot extract page ID from: {url}")


def _resolve_tiny_link(tiny_id: str) -> str:
    # Follow the redirect from the short URL to get the real page URL
    resp = requests.get(
        f"{BASE_URL}/x/{tiny_id}",
        auth=_auth(),
        allow_redirects=True,
    )
    resp.raise_for_status()
    # Extract numeric page ID from the resolved URL
    m = re.search(r"/pages/(\d+)", resp.url)
    if m:
        return m.group(1)
    raise ValueError(f"Could not resolve tiny link {tiny_id} — landed at {resp.url}")


def fetch(url_or_id: str) -> Path:
    page_id = _page_id_from_url(url_or_id)
    resp = requests.get(
        f"{BASE_URL}/rest/api/content/{page_id}",
        auth=_auth(),
        params={"expand": "body.storage,version,space"},
    )
    resp.raise_for_status()
    data = resp.json()

    title = data["title"]
    html_body = data["body"]["storage"]["value"]
    md_body = markdownify(html_body, heading_style="ATX")

    safe_title = re.sub(r"[^\w\-]", "_", title)[:80]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{safe_title}.md"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(
        f"---\n"
        f"source: confluence\n"
        f"url: {BASE_URL}/pages/{page_id}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"title: {title}\n"
        f"---\n\n"
        f"{md_body}\n"
    )
    return out_path
