"""Fetch a Douyin video and save metadata + description + Whisper transcript as Markdown.

Supports:
- Short share URLs: https://v.douyin.com/xxx/
- Full URLs: https://www.douyin.com/video/xxx
- Raw share text pasted from the app (the URL is extracted automatically)

Transcription uses openai-whisper (medium model) via local inference.
ffmpeg must be installed (brew install ffmpeg).
"""
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "raw" / "web"

DOUYIN_URL_RE = re.compile(r"https?://(?:v\.douyin\.com/\S+|(?:www\.)?douyin\.com/video/\S+)")

WHISPER_MODEL = "medium"


def extract_url(text: str) -> str:
    m = DOUYIN_URL_RE.search(text)
    if not m:
        raise ValueError(f"No Douyin URL found in: {text[:100]}")
    return m.group(0).rstrip(".,;:!?，。；：！？")


def _transcribe(url: str) -> str:
    """Download audio and transcribe with Whisper. Returns formatted Markdown or error comment."""
    try:
        import whisper
    except ImportError:
        return "<!-- openai-whisper not installed: pip install openai-whisper -->"

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = Path(tmpdir) / "audio.m4a"

        # Download smallest available format (Douyin has no audio-only streams)
        dl = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "-f", "bytevc1_540p_128424-2/bytevc1_540p_128424-0/worst",
                "-o", str(audio_path),
                "--no-playlist",
                url,
            ],
            capture_output=True, text=True,
        )
        # yt-dlp may add an extension; find what it actually saved
        candidates = list(Path(tmpdir).glob("audio.*"))
        if not candidates:
            return f"<!-- audio download failed: {dl.stderr[:200]} -->"
        actual_audio = candidates[0]

        print(f"  Transcribing with Whisper ({WHISPER_MODEL})… this may take a few minutes")
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(str(actual_audio), language="zh", task="transcribe")

        segments = result.get("segments", [])
        if not segments:
            # Fallback: plain text with no timestamps
            text = result.get("text", "").strip()
            return f"## Transcript\n\n{text}\n" if text else "<!-- Whisper returned empty transcript -->"

        lines = []
        for seg in segments:
            start = int(seg["start"])
            mm, ss = divmod(start, 60)
            hh, mm2 = divmod(mm, 60)
            ts = f"{hh:02d}:{mm2:02d}:{ss:02d}" if hh else f"{mm2:02d}:{ss:02d}"
            text = seg["text"].strip()
            if text:
                lines.append(f"[{ts}] {text}")

        return "## Transcript (Whisper medium, zh)\n\n" + "\n".join(lines) + "\n"


def fetch(url_or_text: str, transcribe: bool = True) -> Path:
    """Fetch Douyin video metadata + transcript and save as Markdown. Returns output path."""
    url = extract_url(url_or_text) if not url_or_text.startswith("http") else url_or_text

    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-download", "--no-playlist", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}: {result.stderr[:300]}")

    d = json.loads(result.stdout)

    title = d.get("title", "untitled")
    description = d.get("description", "")
    channel = d.get("channel", d.get("uploader", ""))
    duration = d.get("duration_string", "")
    upload_date = d.get("upload_date", "")
    video_id = d.get("id", "")
    like_count = d.get("like_count", "")
    comment_count = d.get("comment_count", "")
    repost_count = d.get("repost_count", "")

    date_str = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8
        else datetime.now().strftime("%Y-%m-%d")
    )

    stats_parts = []
    if like_count:
        stats_parts.append(f"👍 {like_count}")
    if comment_count:
        stats_parts.append(f"💬 {comment_count}")
    if repost_count:
        stats_parts.append(f"🔁 {repost_count}")
    stats_line = "  ".join(stats_parts)

    transcript_md = _transcribe(url) if transcribe else "<!-- transcription skipped -->"

    md_body = (
        f"**Creator:** {channel}  \n"
        f"**Duration:** {duration}  \n"
        f"**Published:** {date_str}  \n"
        f"**Stats:** {stats_line}  \n"
        f"**URL:** {url}\n\n"
        f"## Description\n\n{description}\n\n"
        f"{transcript_md}\n"
    )

    safe_title = re.sub(r"[^\w\-]", "_", title)[:60]
    filename = f"{date_str}_douyin.com_{safe_title}.md"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(
        f"---\n"
        f"source: douyin\n"
        f"url: {url}\n"
        f"fetched_at: {datetime.now().isoformat()}\n"
        f"title: {title}\n"
        f"channel: {channel}\n"
        f"video_id: {video_id}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{md_body}\n",
        encoding="utf-8",
    )
    return out_path
