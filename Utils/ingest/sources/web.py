"""Fetch an arbitrary web page and save as Markdown."""
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import trafilatura
from markdownify import markdownify

OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "web"

YOUTUBE_RE = re.compile(r"(youtube\.com/watch|youtu\.be/)")


def fetch(url: str) -> Path:
    if YOUTUBE_RE.search(url):
        return _fetch_youtube(url)

    # trafilatura handles download + boilerplate removal
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Failed to download: {url}")

    html = trafilatura.extract(downloaded, output_format="html", include_comments=False)
    if not html:
        raise RuntimeError(f"Failed to extract content from: {url}")

    md_body = markdownify(html, heading_style="ATX")
    title = _extract_title(downloaded) or urlparse(url).netloc

    domain = urlparse(url).netloc.replace("www.", "")
    safe_title = re.sub(r"[^\w\-]", "_", title)[:60]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{domain}_{safe_title}.md"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(
        f"---\n"
        f"source: web\n"
        f"url: {url}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"title: {title}\n"
        f"---\n\n"
        f"{md_body}\n"
    )
    return out_path


def _extract_video_id(url: str) -> str:
    """Extract YouTube video ID from various URL formats."""
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be",):
        return parsed.path.lstrip("/").split("?")[0]
    qs = parse_qs(parsed.query)
    return qs.get("v", [""])[0]


def _fetch_transcript_api(video_id: str) -> str:
    """Fetch transcript using youtube_transcript_api (v1+). Returns formatted Markdown or error comment."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # Prefer manually-created English; fall back to auto-generated; then any language
        PREFERRED_LANGS = ["en", "zh-Hans", "zh-Hant", "zh", "zh-TW", "zh-CN"]
        fetched = None
        try:
            t = transcript_list.find_manually_created_transcript(PREFERRED_LANGS)
            fetched = t.fetch()
        except Exception:
            pass
        if fetched is None:
            try:
                t = transcript_list.find_generated_transcript(PREFERRED_LANGS)
                fetched = t.fetch()
            except Exception:
                pass
        if fetched is None:
            # grab the first available transcript in any language
            t = next(iter(transcript_list))
            fetched = t.fetch()

        lang_code = fetched.language_code
        is_generated = fetched.is_generated

        # Format as timestamped lines
        lines = []
        for snippet in fetched.snippets:
            start = int(snippet.start)
            mm, ss = divmod(start, 60)
            hh, mm = divmod(mm, 60)
            ts = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            text = snippet.text.strip().replace("\n", " ")
            lines.append(f"[{ts}] {text}")

        note = " *(auto-generated)*" if is_generated else ""
        return f"## Transcript ({lang_code}{note})\n\n" + "\n".join(lines)

    except Exception as exc:
        return f"<!-- transcript unavailable via API: {exc} -->"


def _fetch_transcript_ytdlp(url: str) -> str:
    """Fallback: download subtitle via yt-dlp into a temp dir and read it."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable, "-m", "yt_dlp",
                    "--write-auto-sub", "--write-sub",
                    "--sub-lang", "en,zh-Hans,zh-Hant",
                    "--sub-format", "vtt",
                    "--skip-download",
                    "--no-playlist",
                    "-o", str(Path(tmpdir) / "%(id)s.%(ext)s"),
                    url,
                ],
                capture_output=True, text=True,
            )
            vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                return "<!-- no subtitle files found via yt-dlp -->"

            vtt_path = vtt_files[0]
            raw = vtt_path.read_text(encoding="utf-8", errors="replace")
            # Strip VTT headers and cue metadata, keep only text
            lines = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("WEBVTT") or re.match(r"^\d+$", line):
                    continue
                if re.match(r"^\d{2}:\d{2}", line):  # timestamp line
                    ts = line.split("-->")[0].strip()
                    # convert HH:MM:SS.mmm → MM:SS
                    ts_clean = re.sub(r"\.\d+$", "", ts)
                    lines.append(f"\n[{ts_clean}]")
                    continue
                # Strip VTT inline tags like <00:00:00.000><c>text</c>
                text = re.sub(r"<[^>]+>", "", line).strip()
                if text:
                    lines.append(text)

            lang = vtt_path.stem.split(".")[-1] if "." in vtt_path.stem else "unknown"
            return f"## Transcript ({lang}, via yt-dlp)\n\n" + " ".join(lines)

    except Exception as exc:
        return f"<!-- yt-dlp transcript fallback failed: {exc} -->"


def _fetch_youtube(url: str) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-download", "--no-playlist", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:200]}")

    d = json.loads(result.stdout)
    title = d.get("title", "untitled")
    description = d.get("description", "")
    channel = d.get("channel", "")
    duration = d.get("duration_string", "")
    upload_date = d.get("upload_date", "")  # YYYYMMDD
    date_str = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if upload_date else datetime.now().strftime("%Y-%m-%d")

    # --- Transcript ---
    video_id = _extract_video_id(url) or d.get("id", "")
    if video_id:
        transcript_md = _fetch_transcript_api(video_id)
        # If API failed, try yt-dlp fallback
        if transcript_md.startswith("<!-- transcript unavailable"):
            transcript_md = _fetch_transcript_ytdlp(url)
    else:
        transcript_md = "<!-- could not determine video ID -->"

    md_body = (
        f"**Channel:** {channel}  \n"
        f"**Duration:** {duration}  \n"
        f"**URL:** {url}\n\n"
        f"## Description\n\n{description}\n\n"
        f"{transcript_md}\n"
    )

    domain = "youtube.com"
    safe_title = re.sub(r"[^\w\-]", "_", title)[:60]
    filename = f"{date_str}_{domain}_{safe_title}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(
        f"---\n"
        f"source: youtube\n"
        f"url: {url}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"title: {title}\n"
        f"channel: {channel}\n"
        f"video_id: {video_id}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{md_body}\n"
    )
    return out_path


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else ""
