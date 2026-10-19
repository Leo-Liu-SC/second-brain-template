---
name: second-brain-update
description: Extract durable knowledge and personal actions from all ingested Second Brain content — meeting notes, Confluence pages, web articles, YouTube/Douyin video transcripts, and Slack threads — present a review draft, then update wiki articles and (if configured) a Todoist MCP only after explicit approval. Use when the user says "update second brain", "update the wiki", or asks to process, review, summarize, compile, or promote any files from raw/ into the wiki; when handling discussion, decision, workshop, training, presentation, sharing-session, or article notes; or when the user asks what recent ingested content should add to the knowledge base. Does NOT fetch or sync new data — assumes raw/ already has the files to process.
---

# Update Second Brain

Process ingested content and promote durable knowledge to the wiki. Keep raw notes immutable and personal actions in a Todoist MCP (if the user has one configured) rather than the wiki.

The wiki records **what is true now**, not what was said when. An article is a current-state document with an audit trail, not a chronological meeting log.

## Source types and approval rules

| Source type | Files in | Review gate required? |
|---|---|---|
| Meeting notes (Zoom, 1:1s, syncs) | `raw/meetings/` | **Yes** — always stop for approval |
| Web articles, YouTube/Douyin transcripts | `raw/web/` | **No** — apply directly after extraction |
| Confluence pages | `raw/confluence/` | **No** — apply directly after extraction |
| Slack threads / announcements | `raw/slack/` | **No** — apply directly after extraction |

For non-meeting sources: extract knowledge items, state what will be merged/created, apply immediately, and report what was written.

Three exceptions override "apply directly" for **every** source type, meetings included:

- A `CONTRADICTS` conflict (step 3.5) always stops for the user.
- Creating a new wiki article always stops for the user.
- An external source (web, video, vendor material) may only be written as a `PROPOSED` or `OBSERVED` claim attributed to that source. It never becomes an unattributed fact about the user's own organization or team.

## The article contract

Every wiki article uses this skeleton, in this order. Sections with nothing in them are omitted, never reordered.

```markdown
# <Title>

<One-paragraph summary — what this article covers and why it matters.>

## Current position
<Facts true as of today. Rewritten in place. Every bullet carries a tag and a date.>

## Under discussion
<Open questions. What is unresolved, who is arguing what, and what would settle it.>

## Decisions
<Append-only ledger, date-led and untagged. One dated line per decision, newest first.>

## History / superseded
<Append-only. Facts dislodged from Current position, each pointing at what replaced it.>

## Sources
<Append-only provenance list: source file, title, date.>

## Related topics
<[[wikilinks]].>
```

**`Sources` and `Related topics` are always the final sections.** Appending content after them is the single most common failure and the lint treats it as breakage.

### What "merge" means

Merging a knowledge item means one or more of:

- **Rewrite** a `Current position` bullet so it states the newer fact, and move the old wording to `History / superseded`.
- **Append** one line to `Decisions`.
- **Add or resolve** an entry in `Under discussion`.
- **Append** the source to `Sources`.

Merging never means adding a new section named after a meeting. Headings describe *subjects*, not events — `## Current position`, not `## Car Hire WBR — Aug 25`. The meeting's identity lives in `Sources` and in the per-bullet date.

### Repeated metrics

A metric that gets re-reported every week belongs in one table inside `Current position`, with an `as of` column — not in a fresh paragraph per reading. A new reading updates the row and, when the movement itself is meaningful, adds one line to `History / superseded`.

## Epistemic tags

Every bullet in `Current position` and `Under discussion` opens with exactly one tag. Untagged bullets are not written.

| Tag | Means | Requires |
|---|---|---|
| `DECIDED` | An explicit decision was made | date + decision owner |
| `AGREED` | Direction agreed without a formal decision | date + who agreed |
| `PROPOSED` | Someone proposed it; not agreed | proposer |
| `DISCUSSED` | Aired with no conclusion | — |
| `DISPUTED` | Two credible sources conflict and neither clearly supersedes | both claims + both sources |
| `OBSERVED` | A metric, result, or event | value + date |
| `INFERRED` | Your reading, not stated in the source | what it is inferred from |

Every `Current position` bullet also ends with `(as of YYYY-MM-DD)` — the date of the source that last confirmed it, not today's date.

`Decisions` is the exception: its entries are date-led (`- 2026-09-16 · <decision>`) and carry no tag, because the section name already supplies the status. Writing `DECIDED` on every line of a section called Decisions is noise.

This is what makes discussion distinguishable from conclusion at a glance, and it is what the single-source-bias rule needs in order to be checkable: a claim resting on one person's view is `PROPOSED` or `DISCUSSED`, never `DECIDED`.

## Workflow

### 1. Establish scope

- Resolve the Second Brain root from the current workspace.
- Read `CLAUDE.md` and `wiki/meeting-processing-log.md`. (Skip `wiki/INDEX.md` — already in session context via the startup hook.)
- **Determine unprocessed files deterministically**:
  1. Run `ls raw/meetings/` to get the full file list.
  2. Cross-reference against the filenames already in `wiki/meeting-processing-log.md`.
  3. The delta is the unprocessed set. State it explicitly.
  4. Also check `raw/web/`, `raw/confluence/`, and `raw/slack/` for files not yet referenced in any wiki article.
- **Batching**: With more than 7 unprocessed files, process the most recent 5–7 by filename date and explicitly name the rest as a second batch. Never silently skip files.
- Never modify `raw/`.
- **Reading meeting files**: For Zoom files read only the `## Zoom AI Summary` section. Read the full transcript only if that section is absent or under 100 words.
- Search existing wiki articles for the meeting title, date, source filename, and major concepts to prevent duplicate promotion.

### 2. Extract knowledge — subtractively

Classify each meeting as `discussion`, `sharing`, or `hybrid`; non-meeting sources as `article`, `announcement`, or `transcript`.

Extraction is a filter, not a transcription. Caps are hard:

- **Max 5 `K` items per source file.**
- **Max 1 `K` item per target article per source file.** Two facts for the same article get synthesised into one item or one is dropped.

Admission test — a candidate must pass both:

1. Would the user need this to make or defend a decision three months from now?
2. Does it change what the target article currently says?

Reject on sight: greetings, attendance, ceremony mechanics, a metric restated with no delta, status with no continuing value, and anything that merely records that a meeting or presentation happened. Synthesise — never paste source prose.

Assign stable IDs: `K1`, `K2`, … for knowledge; `A1`, `A2`, … for actions.

For each `K` item record the tag it will carry, the target article, and `merge` / `create` / `skip` with a reason.

### 3. Reconcile against the target article — every K item, every source type

Before proposing any write, read the target article's `Current position` and grep it for the same entity, metric, project, or person. Classify each `K` item:

| Class | Meaning | Handling |
|---|---|---|
| `NEW` | Article says nothing about it | Add a `Current position` bullet |
| `REFINES` | Adds detail without changing the existing claim | Extend the existing bullet in place |
| `SUPERSEDES` | Newer source, same subject, different fact | Rewrite the bullet; move the old wording to `History / superseded` with `superseded YYYY-MM-DD by <source>` |
| `CONTRADICTS` | Two credible claims and it is genuinely unclear which holds | **Do not pick a winner** — see below |
| `DUPLICATE` | Already stated with equal or better precision | `skip` |

`SUPERSEDES` versus `CONTRADICTS` turns on one question: is the newer source plainly the later state of the same thing? If yes it supersedes. If the two could both be true, or the sources disagree on fact rather than on timing, it contradicts.

**Handling `CONTRADICTS`:** write a single `DISPUTED` bullet carrying both claims, both sources, and both dates. Never silently drop either side and never average them. Surface it in a `## Conflicts detected` block at the **top** of the review draft, and stop for the user even for non-meeting sources — resolving a contradiction is the user's judgement, not yours.

State the classification for every `K` item in the review draft. An item with no stated class is not ready to write.

### 4. Stop for review — meeting notes, conflicts, and new articles

Required for meeting notes, any `CONTRADICTS` item, and any new-article creation. Skip only for non-meeting sources with no conflicts and no new articles.

Use the structure in `references/review-format.md`. While awaiting approval:

- Do not modify any wiki file, including `wiki/INDEX.md`.
- Do not append to `inbox/pending-tasks.md`.
- Do not call any Todoist MCP write tool.

Ask the user to approve all items or specify IDs, corrections, rejections, target-page changes, task wording, or due dates.

Treat only an explicit response such as "approve all" or "approve K1-K4 and A2" as approval. A request to explain, revise, or inspect is not approval.

### 5. Apply knowledge

For approved `K` items (or all extracted items where step 4 was skipped):

- Write into the skeleton per **The article contract** above, using the reconciliation class from step 3.
- Merge into an existing article whenever possible. Create a new article only for a distinct durable topic that cannot fit an existing page — and only with approval.
- Preserve the opening summary paragraph.
- Every new bullet carries its epistemic tag; `Current position` bullets carry `(as of YYYY-MM-DD)`.
- Append provenance to `Sources`. Do not repeat provenance inline on every bullet.
- Add `[[topic-name]]` links where genuinely useful.
- Update `wiki/INDEX.md` only when an article is created or its one-line description is materially stale.
- Never add action lists, task checkboxes, or follow-up reminders to wiki articles.
- Preserve unrelated user edits in the dirty worktree.

Then run the lint and fix every error before reporting:

```bash
python3 Utils/lint/lint_wiki.py --changed
```

Errors block completion. Warnings on articles you did not touch are pre-existing migration debt — report the count, do not fix them inline.

### 6. Create approved personal actions

This step only applies if the user has a Todoist MCP configured (see README for setup). If not, skip it entirely and report action items as text in step 7 instead.

For approved `A` items only:

- Create a Todoist task only when the user is the explicit owner, or they explicitly reassign it to themselves.
- Do not create tasks for teams, squads, or other named owners.
- If ownership is ambiguous, ask before creating.
- Before writing, query Todoist for a likely duplicate using the action's distinctive terms.
- Use a concise verb-led title.
- Preserve an explicit due date; never invent one. Resolve a relative date from the meeting date and show the resolved date during review.
- Put the meeting title, date, source file, and context in the task description.
- If the Todoist MCP is unavailable, do not substitute another task system. Return the payload and state that the write is blocked.

Actions never go into wiki files, including `my-actions-tracker.md`, and not into `inbox/pending-tasks.md` under this workflow.

### 7. Report completion

Return:

- Knowledge IDs applied, with wiki file links and reconciliation class.
- Facts superseded, and where the old wording went.
- Conflicts recorded as `DISPUTED` and still open.
- Action IDs created, with Todoist identifiers when available.
- Rejected, skipped, ambiguous, or blocked items.
- Lint result: error count (must be zero) and warning count.

Record each processed file in `wiki/meeting-processing-log.md`, including supersessions and conflicts — not merges alone. The log is the audit trail that lets a later run tell already-promoted knowledge from genuinely new material.

Do not claim completion for an item that was not actually written.

## Safety rules

- **Meeting notes**: keep extraction and mutation in separate turns. Never infer approval from silence or from the original request to process.
- **Conflicts and new articles**: always stop, regardless of source type.
- **External sources**: only ever `PROPOSED` or `OBSERVED` with attribution. Never unattributed fact.
- Never promote unsupported interpretation as fact — tag it `INFERRED`.
- Never overwrite an entire wiki article when a targeted merge is sufficient.
- Never resolve a contradiction by choosing the more recent source unless it is plainly the later state of the same thing.
- Never create duplicate Todoist tasks.
