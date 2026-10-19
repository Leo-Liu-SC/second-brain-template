#!/usr/bin/env python3
"""Structural lint for the Second Brain wiki.

Enforces the article contract defined in .claude/skills/second-brain-update/SKILL.md.

Two severities:
  ERROR  - contract violations. Fail the run.
  WARN   - legacy debt (articles not yet migrated to the skeleton, oversized
           articles). Reported, never fails.

An article is "migrated" once it contains a `## Current position` heading.
Un-migrated articles only get WARNs, so the lint is usable during migration
instead of drowning in 8k lines of legacy output.

Usage:
    python3 Utils/lint/lint_wiki.py              # lint every article
    python3 Utils/lint/lint_wiki.py --changed    # only git-changed articles
    python3 Utils/lint/lint_wiki.py --hook       # stderr + exit 2 on ERROR
    python3 Utils/lint/lint_wiki.py --hook --advisory   # stderr, never fails

--advisory exists because the wiki carries pre-existing contract violations.
Drop the flag from the Stop hook in .claude/settings.json once
`python3 Utils/lint/lint_wiki.py` reports 0 errors, and the lint starts
blocking instead of reporting.
"""

import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIKI = os.path.join(REPO, "wiki")

# Files that are registers/indexes, not knowledge articles.
EXEMPT = {"INDEX.md", "meeting-processing-log.md"}
# Legacy action file the skill no longer writes to; flagged once, not per-line.
LEGACY_ACTION_FILES = {"my-actions-tracker.md"}

MAX_LINES = 400

TAGS = [
    "DECIDED", "AGREED", "PROPOSED", "DISCUSSED", "DISPUTED",
    "OBSERVED", "INFERRED",
]
TAG_RE = re.compile(r"^\s*[-*]\s+(?:\*\*)?(" + "|".join(TAGS) + r")\b")
BULLET_RE = re.compile(r"^\s*[-*]\s+\S")
# The Decisions ledger is date-led: the section name already supplies the
# epistemic status, so "- 2026-09-16 - ..." needs no DECIDED prefix.
DATE_LED_RE = re.compile(r"^\s*[-*]\s+\d{4}-\d{2}(?:-\d{2})?\b")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
AS_OF_RE = re.compile(r"as of \d{4}-\d{2}-\d{2}", re.IGNORECASE)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

# Chinese headings included: a "## 行动项" section survived an
# English-only check for months.
BANNED_HEADINGS = re.compile(
    u"^(action items?|actions|follow[- ]ups?|next steps?|todos?|my actions"
    u"|行动项|行动计划|待办|待办事项"
    u"|后续行动|下一步)$",
    re.IGNORECASE,
)

TAGGED_SECTIONS = {"current position", "under discussion", "decisions"}
TRAILING = {"sources", "related topics"}


class Report(object):
    def __init__(self):
        self.errors = []
        self.warns = []

    def error(self, path, line, msg):
        self.errors.append((path, line, msg))

    def warn(self, path, line, msg):
        self.warns.append((path, line, msg))


def rel(path):
    return os.path.relpath(path, REPO)


def article_files():
    out = []
    for name in sorted(os.listdir(WIKI)):
        if name.endswith(".md") and name not in EXEMPT:
            out.append(os.path.join(WIKI, name))
    return out


def changed_files():
    try:
        raw = subprocess.check_output(
            ["git", "-C", REPO, "status", "--porcelain", "--", "wiki"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    except (subprocess.CalledProcessError, OSError):
        return article_files()
    out = []
    for line in raw.splitlines():
        p = line[3:].strip().strip('"')
        name = os.path.basename(p)
        if p.endswith(".md") and name not in EXEMPT:
            full = os.path.join(REPO, p)
            if os.path.exists(full):
                out.append(full)
    return sorted(set(out))


def sections(lines):
    """Yield (heading_text_lower, heading_line_no, body_line_numbers)."""
    heads = []
    for i, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            heads.append((m.group(2).strip().lower(), i))
    for idx, (title, ln) in enumerate(heads):
        end = heads[idx + 1][1] - 1 if idx + 1 < len(heads) else len(lines)
        yield title, ln, range(ln + 1, end + 1)


def lint_article(path, report, known_slugs):
    with open(path, "r") as fh:
        text = fh.read()
    lines = text.split("\n")
    name = os.path.basename(path)
    migrated = any(
        t == "current position" for t, _, _ in sections(lines)
    )
    sev = report.error if migrated else report.warn

    if len(lines) > MAX_LINES:
        report.warn(path, 1, "%d lines (>%d) - queue for /second-brain-compact"
                    % (len(lines), MAX_LINES))

    if not migrated:
        report.warn(path, 1, "no '## Current position' - not migrated to the "
                             "article skeleton yet")

    # Summary paragraph: first non-heading, non-blank line after the H1.
    body_started = False
    for i, line in enumerate(lines, 1):
        if HEADING_RE.match(line):
            if body_started:
                report.error(path, i, "no summary paragraph between the title "
                                      "and the first section")
                break
            body_started = True
            continue
        if body_started and line.strip():
            break

    # Rule: no action lists anywhere in the wiki.
    if name not in LEGACY_ACTION_FILES:
        for i, line in enumerate(lines, 1):
            if CHECKBOX_RE.match(line):
                report.error(path, i, "task checkbox in a wiki article - "
                                      "actions belong in Todoist")
    else:
        report.warn(path, 1, "legacy action file - retire its contents into Todoist")

    # Banned headings at any depth (### Action Items slips past an H2-only check).
    if name not in LEGACY_ACTION_FILES:
        for i, line in enumerate(lines, 1):
            m = HEADING_RE.match(line)
            if m and BANNED_HEADINGS.match(m.group(2).strip()):
                report.error(path, i, "'%s' heading - actions belong in "
                                      "Todoist, not the wiki" % m.group(2).strip())

    ordered = list(sections(lines))
    for title, ln, body in ordered:
        if title in TAGGED_SECTIONS:
            for i in body:
                line = lines[i - 1]
                if BULLET_RE.match(line) and not line.startswith("  "):
                    dated_ledger = (title == "decisions"
                                    and DATE_LED_RE.match(line))
                    if not TAG_RE.match(line) and not dated_ledger:
                        sev(path, i, "untagged bullet in '%s' - must start "
                                     "with one of %s"
                            % (title, "/".join(TAGS)))
                    elif title == "current position" and not AS_OF_RE.search(line):
                        sev(path, i, "Current position bullet has no "
                                     "'(as of YYYY-MM-DD)'")

    # Sources and Related topics must occupy the final slots, in either order.
    titles = [t for t, _, _ in ordered]
    n_trailing = len([t for t in titles if t in TRAILING])
    for pos, title in enumerate(titles):
        if title in TRAILING and pos < len(titles) - n_trailing:
            sev(path, ordered[pos][1],
                "'%s' must be one of the last %d sections (found at position "
                "%d of %d) - content was appended after it"
                % (title, n_trailing, pos + 1, len(titles)))

    # Wikilinks resolve.
    for i, line in enumerate(lines, 1):
        for m in LINK_RE.finditer(line):
            slug = m.group(1).strip()
            if slug and slug.lower() not in known_slugs:
                report.error(path, i, "broken wikilink [[%s]]" % slug)


def lint_index(paths, report):
    index = os.path.join(WIKI, "INDEX.md")
    if not os.path.exists(index):
        report.error(index, 1, "wiki/INDEX.md is missing")
        return
    with open(index, "r") as fh:
        body = fh.read()
    listed = set(m.group(1).strip().lower() for m in LINK_RE.finditer(body))
    for path in paths:
        slug = os.path.basename(path)[:-3]
        if slug.lower() not in listed:
            report.error(path, 1, "not listed in wiki/INDEX.md")


def main(argv):
    hook = "--hook" in argv
    advisory = "--advisory" in argv
    only_changed = "--changed" in argv

    all_articles = article_files()
    known = set(os.path.basename(p)[:-3].lower() for p in all_articles)
    known |= set(n[:-3].lower() for n in EXEMPT)

    targets = changed_files() if only_changed else all_articles
    report = Report()
    for path in targets:
        lint_article(path, report, known)
    lint_index(targets, report)

    stream = sys.stderr if hook else sys.stdout

    def emit(rows, label):
        for path, line, msg in rows:
            stream.write("%s:%d: %s: %s\n" % (rel(path), line, label, msg))

    emit(report.errors, "ERROR")
    if not hook or not report.errors:
        emit(report.warns, "warn")
    stream.write("\nwiki lint: %d file(s), %d error(s), %d warning(s)\n"
                 % (len(targets), len(report.errors), len(report.warns)))

    if report.errors and not advisory:
        return 2 if hook else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
