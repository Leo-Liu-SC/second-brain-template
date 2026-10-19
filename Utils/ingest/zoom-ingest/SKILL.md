---
name: zoom-ingest
description: Ingest completed Zoom meetings into the Second Brain as immutable Markdown source notes, using the connected Zoom app to retrieve meeting metadata, AI summaries, next steps, assets, playback links, and full speaker-attributed transcripts. Use when asked to ingest, import, sync, fetch, download, or save one or more Zoom meetings or recordings into raw/meetings; when asked to capture the latest or recent Zoom meeting; or when replacing the legacy browser-based Zoom ingestion workflow with connector-backed ingestion.
---

# Zoom Ingest

Ingest completed Zoom meetings through the connected Zoom app and create one
source note per meeting under `raw/meetings/`. Preserve raw notes, avoid
duplicates, and leave knowledge promotion and task creation to downstream
workflows.

## Workflow

### 1. Establish scope

- Resolve the Second Brain root by locating `raw/meetings/` and
  `Utils/ingest/`. When invoked from this skill folder, the root is three
  directories above `SKILL.md`.
- Read the applicable `AGENTS.md` and repository instructions before writing.
- Interpret dates in the user's stated timezone. Otherwise use the environment
  timezone, falling back to the user's configured local timezone if neither
  is available.
- Convert the search range to UTC for Zoom tool calls.
- Resolve the requested meeting set:
  - A meeting ID or UUID selects that exact meeting occurrence.
  - "Latest" selects the most recent completed meeting found in the last seven
    days.
  - "Recent" without another range selects completed meetings from the last
    seven days.
  - An explicit date range selects all completed meetings within it.
  - A title, participant, or topic narrows the Zoom meeting search.
- If a topic search has multiple plausible matches and the user did not ask for
  all of them, ask the user to select from the Zoom search UI. Do not reproduce
  the connector's raw meeting list in chat.
- If the resolved scope exceeds 30 days or 20 meetings, report the count and ask
  for confirmation before retrieving full transcripts or writing files.

### 2. Require the Zoom connector

- Use the connected Zoom app or Zoom MCP-backed connector.
- If Zoom tools are unavailable or authentication fails, tell the user to
  install or authorize the Zoom plugin and stop.
- Do not fall back to `Utils/ingest/sources/zoom.py`, browser automation,
  Playwright, stored browser sessions, or Zoom Docs unless the user explicitly
  requests a different workflow.
- Use read-only Zoom operations. Never create or update Zoom Docs.

### 3. Discover meetings

- Use the Zoom meeting-search tool for natural-language, date, participant, or
  topic queries.
- Use an exact meeting UUID directly when the user supplies one.
- Paginate until every result in the approved scope has been considered.
- Exclude upcoming and in-progress meetings.
- Prefer the meeting UUID over the numeric meeting number for every downstream
  call. Apply Zoom's required double URL encoding when the UUID begins with `/`
  or contains `//`.

### 4. Check for duplicates before fetching transcripts

Search existing Markdown files in `raw/meetings/` before creating anything:

1. If `meeting_uuid` already exists, skip the meeting.
2. For legacy notes without a UUID, compare the local meeting date and a
   normalized title. Treat an exact date-and-title match as already ingested
   and report it as a `legacy-match`.
3. If several legacy files are plausible matches, do not guess or overwrite
   them. Skip that meeting and report the ambiguous paths for review.

Never edit, replace, rename, or delete an existing file under `raw/`.

### 5. Retrieve complete meeting content

For each new meeting:

- Fetch meeting-level assets to obtain available summaries, documents,
  whiteboards, and recording metadata.
- Fetch recording resources with `transcript`, `summary`, `nextStep`, and
  `playUrl`.
- Preserve the full transcript returned by Zoom by default. Keep speaker names
  and timestamps.
- Do not invent missing speakers, decisions, actions, dates, or summaries.
- If Zoom has no transcript, retain the summary and next steps and explicitly
  mark the transcript unavailable.
- If no substantive summary, next steps, transcript, or useful assets exist, do
  not create an empty note. Report the meeting as `no-content`.
- Never store OAuth tokens, JWTs, recording passcodes, connector credentials, or
  raw API responses. Store only the playback URL returned for normal user
  access.

### 6. Create the raw Markdown note

Use the local meeting date and a filesystem-safe title:

```text
raw/meetings/YYYY-MM-DD_<normalized-title>.md
```

If two distinct meeting UUIDs would produce the same filename, append a short,
non-sensitive UUID-derived suffix. Write UTF-8 Markdown only when the
destination does not already exist.

Use this structure, omitting optional sections only when Zoom did not return
them:

```markdown
---
source: zoom
meeting_uuid: "<uuid>"
meeting_number: "<number>"
title: "<title>"
date: "YYYY-MM-DD"
started_at: "<ISO-8601 timestamp>"
timezone: "Asia/Shanghai"
fetched_at: "<ISO-8601 timestamp>"
playback_url: "<URL or empty string>"
---

# <Meeting title>

## Zoom AI Summary

<overall and topic summaries>

## Next Steps

<Zoom-generated next steps, preserving attribution when available>

## Transcript

### 00:00:12 — Speaker name

<spoken text>

## Related Assets

- <asset type and accessible link or identifier>
```

Quote YAML string values safely. Keep returned content faithful to Zoom;
perform only light formatting and whitespace cleanup.

### 7. Report the result

Return:

- Created meeting notes with clickable file links.
- Meetings skipped by UUID or legacy date-title match.
- Ambiguous legacy matches requiring review.
- Meetings with missing transcripts or no usable content.
- Zoom authentication, retrieval, pagination, or write failures.

Do not claim a meeting was ingested unless its file was successfully written.

## Boundaries

- Create new raw meeting files only; never mutate existing raw content.
- Do not edit the wiki or `wiki/INDEX.md`.
- Do not create Todoist tasks or promote next steps into another task system.
- Do not summarize or reinterpret the meeting beyond Zoom-provided summaries.
- Use `update-meeting-notes` separately when the user wants reviewed knowledge
  promotion or personal action creation.
