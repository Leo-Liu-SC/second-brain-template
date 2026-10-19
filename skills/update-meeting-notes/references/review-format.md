# Review format

Use this structure before any wiki or Todoist write.

Conflicts come first: they are the highest-value human decision in the draft and the easiest to miss at the bottom of a long review.

```markdown
# Second Brain review

Scope: <source files>
No changes have been made.

## Conflicts detected

Include this section only when a K item classified as CONTRADICTS. Omit it entirely otherwise — never write "none".

- C1 · CONTRADICTS → [[target-article]]
  - Existing: <current wiki claim> (source, date)
  - New: <incoming claim> (source, date)
  - Why not a supersession: <why the newer source is not plainly the later state of the same thing>
  - Proposal: record as DISPUTED holding both, or tell me which holds

## <Source title> · <date>

Type: discussion | sharing | hybrid | article | announcement | transcript

### Topics and key points

- <topic>: <supported key points>

### Conclusions

- <decision, conclusion, or "No explicit conclusion recorded">

### Sharing summary

Include only for sharing or hybrid meetings and for articles or transcripts.

- Shared: <material, framework, demo, research, or update>
- Key takeaways: <reusable lessons>

### Proposed wiki knowledge

- K1 · MERGE · SUPERSEDES → [[target-article]] › Current position
  - Tag: DECIDED (2026-09-18, owner)
  - Proposed bullet: <the exact line that will be written, tag and date included>
  - Replaces: <the existing wiki wording, which moves to History / superseded>
  - Evidence: <concise source wording>
  - Why durable: <reason>

- K2 · MERGE · NEW → [[target-article]] › Decisions
  - Tag: AGREED (2026-09-18, who agreed)
  - Proposed bullet: <exact line>
  - Evidence: <concise source wording>

- K3 · SKIP · DUPLICATE
  - Content: <transient or already-captured information>
  - Reason: <why it should not enter the wiki>

### Proposed personal actions

- A1 · ELIGIBLE
  - Task: <verb-led task title>
  - Owner evidence: <why the user is explicitly the owner>
  - Due: <YYYY-MM-DD or none>
  - Source: <file and section>

- A2 · NOT ELIGIBLE
  - Action: <action wording>
  - Owner: <other person, team, or ambiguous>
  - Reason: <why no Todoist task will be created>

## Approval request

Reply with one of:

- `Approve all eligible items`
- `Approve K1, K3, and A2`
- `Reject K2`
- For a conflict: `C1: the new claim holds` / `C1: keep both as DISPUTED`
- Corrections to wording, tag, target section, ownership, or due date
```

## Every K item needs four things

A `K` item is not ready to review without all of these. A missing one means go back to step 2 or 3, not write with a guess.

1. **Target** — article *and* section (`› Current position`, `› Decisions`, `› Under discussion`).
2. **Reconciliation class** — `NEW` / `REFINES` / `SUPERSEDES` / `CONTRADICTS` / `DUPLICATE`, established by actually reading the target article.
3. **Tag** — one of `DECIDED` / `AGREED` / `PROPOSED` / `DISCUSSED` / `DISPUTED` / `OBSERVED` / `INFERRED`, with what that tag requires.
4. **The exact bullet** — the literal line to be written, not a description of it. The user approves text, not intent.

## Extraction distinctions

- A topic is what participants discussed; it is not automatically durable knowledge.
- A conclusion records an explicit decision, agreement, resolved position, or confirmed outcome. If the source does not contain one, say so — do not upgrade a discussion into a conclusion.
- One person asserting something in a 1:1 is `PROPOSED`, not `DECIDED`, however confidently they said it.
- An action requires an action verb and an owner. Do not turn general intent into a task.
- A sharing summary explains what was presented and what is reusable, not merely that a presentation occurred.
- A wiki proposal synthesises; it does not paste source prose.
- Caps are hard: max 5 `K` items per source, max 1 per target article per source. If a source seems to warrant more, the items are not synthesised enough.

## Approval interpretation

- `Approve all` approves eligible `K` items and user-owned eligible `A` items. It does **not** resolve a `C` conflict — a conflict needs its own instruction.
- Approval of an ineligible or ambiguous action requires clarification before task creation.
- Corrections replace the draft wording but do not authorize unrelated items.
- If the user approves knowledge but says nothing about actions, do not create tasks.
- If the user approves actions but says nothing about knowledge, do not update wiki files.
- If the user says nothing about a conflict, leave the wiki untouched for that item and report it as still open.
