# Knowledge Base Schema

## What This Is
A personal knowledge base — a curated collection of insights maintained by AI.

## How It's Organized
- raw/ contains unprocessed source material. Never modify these files.
- wiki/ contains the organized wiki. AI maintains this entirely.
- outputs/ contains generated reports, answers, and analyses.

## Wiki Rules
- Every topic gets its own .md file in wiki/
- Every wiki file starts with a one-paragraph summary
- Link related topics to each other using [[topic-name]] format
- Maintain an INDEX.md in wiki/ that lists every topic with a one-line description
- When new raw sources are added, update the relevant wiki articles
- Articles record what is true now, not what was said when. Section order is fixed: summary, Current position, Under discussion, Decisions, History / superseded, Sources, Related topics. Headings name subjects, never meetings. Sources and Related topics are always last.
- Every bullet in Current position / Under discussion opens with one epistemic tag: DECIDED, AGREED, PROPOSED, DISCUSSED, DISPUTED, OBSERVED, or INFERRED. Current position bullets also end with `(as of YYYY-MM-DD)`. Decisions entries are date-led and untagged — the section name supplies the status.
- Never resolve two conflicting sources by picking the newer one unless it is plainly the later state of the same thing. Otherwise record one DISPUTED bullet holding both and ask.
- The full contract lives in `skills/update-meeting-notes/SKILL.md` (exposed as the `second-brain-update` skill via the symlink in `.claude/skills/`).

## Wiki Lint

`python3 Utils/lint/lint_wiki.py [--changed]` enforces the article contract: no task checkboxes or action headings, fixed trailing sections, tagged and dated bullets, resolving wikilinks, INDEX coverage, and a 400-line compaction threshold.

Errors are contract violations; warnings are migration debt on articles not yet moved to the skeleton. It also runs as a Stop hook (see `.claude/settings.json`).

## Ingest Layer Separation

The ingest layer (`Utils/ingest/`) only fetches data to `raw/`. All processing — task extraction, wiki updates, and content analysis — happens in the agent layer. Never put processing logic into the ingest Python code.

## Wiki Update Workflow

When processing files under `raw/meetings/`, `raw/web/`, `raw/confluence/`, or any other ingested content, use the `second-brain-update` skill. The skill handles the full review gate and wiki updates, and (if you've configured a Todoist MCP) personal task creation.

## Ingestion Tool

**Location**: `Utils/ingest/ingest.py`

**Usage**:
```bash
cd Utils/ingest && python3 ingest.py zoom --days 7
cd Utils/ingest && python3 ingest.py slack <channel-url> --days 7
cd Utils/ingest && python3 ingest.py confluence <page-url>
cd Utils/ingest && python3 ingest.py web <url>
cd Utils/ingest && python3 ingest.py douyin <url-or-share-text>
cd Utils/ingest && python3 ingest.py doctor
```

**Daily auto-ingestion** (cron): `scheduler.py` runs at 12:30 and 18:00 daily.
Re-install with `bash Utils/ingest/install_cron.sh`.

## My Interests
*(customise this list — it guides how the wiki is organised)*
- Engineering leadership
- People management
- Product vision and strategy
- Delivery
- AI expertise
