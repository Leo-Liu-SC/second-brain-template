# Second Brain Template

A personal AI knowledge system that automatically ingests your meetings, Slack, Confluence, web, and Douyin content into a searchable wiki — surfaced automatically in every Claude Code session.

## Quick Start

```bash
# 1. Copy this template to your preferred location
cp -r second-brain-template ~/Documents/my-second-brain
cd ~/Documents/my-second-brain

# 2. Run the setup wizard (no coding required)
bash setup.sh
```

The setup script handles everything: credentials, dependencies, Claude Code hooks, and cron schedule.

## What's included

| Path | Purpose |
|---|---|
| `Utils/ingest/` | Ingestion CLI, scheduler, and health-check (`doctor`) |
| `Utils/ingest/sources/` | Zoom, Slack, Confluence, web, and Douyin fetchers |
| `Utils/lint/lint_wiki.py` | Structural lint enforcing the wiki article contract |
| `skills/update-meeting-notes/` | The `second-brain-update` skill — review-gated wiki updates |
| `Utils/ingest/zoom-ingest/` | The `zoom-ingest` skill — connector-based Zoom ingestion |
| `hooks/` | Claude Code session hooks |
| `wiki/` | Your organised knowledge base |
| `raw/` | Raw ingested content (never edit) |

## Manual ingestion commands

```bash
cd Utils/ingest

python3 ingest.py zoom --days 30              # Zoom meeting notes (browser SSO)
python3 ingest.py slack <channel-url>         # Slack channel
python3 ingest.py confluence <page-url>       # Confluence page
python3 ingest.py web <url>                   # Any web page / YouTube
python3 ingest.py douyin <url-or-share-text>  # Douyin video (+ Whisper transcript)
python3 ingest.py doctor                      # Check ingestion, scheduler, and wiki health
```

## Updating the wiki

Once content lands in `raw/`, ask Claude Code to "update second brain" — this invokes the
`second-brain-update` skill, which reviews meeting notes with you before writing (other
source types apply directly), enforces the article contract, and runs the wiki lint.
See `skills/update-meeting-notes/SKILL.md` for the full contract.

If you want the skill to also create personal action items, configure a Todoist MCP
server in Claude Code — the skill creates tasks through it when one is available, and
otherwise just reports action items as text.

## Re-login Zoom (monthly)

The Zoom browser session expires roughly monthly:

```bash
cd Utils/ingest
python3 ingest.py zoom --login
```

## Add sources

Edit `Utils/ingest/source_urls.md` to add permanent sources that run on every scheduled sync.

## Check logs

```bash
tail -50 Utils/ingest/logs/cron.log
```
