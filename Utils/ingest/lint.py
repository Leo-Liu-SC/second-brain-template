#!/usr/bin/env python3
"""
Second Brain Lint - Health check for your knowledge base

Detects:
1. Contradictions between wiki articles
2. Gaps (topics in raw/ not covered in wiki/)
3. Unsupported claims (wiki statements without raw sources)
4. Stale articles (wiki files that haven't been updated despite new raw content)

Usage:
    python3 lint.py                    # Full lint with AI analysis
    python3 lint.py --quick            # Quick structural checks only
    python3 lint.py --report report.md # Save report to file
"""

import re
import json
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List

# Paths
SECOND_BRAIN = Path(__file__).resolve().parents[2]
RAW_DIR = SECOND_BRAIN / "raw"
WIKI_DIR = SECOND_BRAIN / "wiki"
OUTPUT_DIR = SECOND_BRAIN / "outputs"


class LintReport:
    def __init__(self):
        self.contradictions = []
        self.gaps = []
        self.unsupported_claims = []
        self.stale_articles = []
        self.broken_links = []
        self.suggestions = []

    def add_contradiction(self, article1: str, article2: str, topic: str, issue: str):
        self.contradictions.append({
            "type": "contradiction",
            "articles": [article1, article2],
            "topic": topic,
            "issue": issue
        })

    def add_gap(self, topic: str, raw_sources: List[str]):
        self.gaps.append({
            "type": "gap",
            "topic": topic,
            "raw_sources": raw_sources
        })

    def add_unsupported_claim(self, article: str, claim: str):
        self.unsupported_claims.append({
            "type": "unsupported",
            "article": article,
            "claim": claim
        })

    def add_stale_article(self, article: str, last_modified: str, new_raw_count: int):
        self.stale_articles.append({
            "type": "stale",
            "article": article,
            "last_modified": last_modified,
            "new_raw_count": new_raw_count
        })

    def add_suggestion(self, suggestion: str):
        self.suggestions.append(suggestion)

    def add_broken_link(self, article: str, target: str):
        self.broken_links.append({
            "type": "broken_link",
            "article": article,
            "target": target,
        })

    def has_issues(self) -> bool:
        return bool(self.contradictions or self.gaps or
                   self.unsupported_claims or self.stale_articles or
                   self.broken_links)

    def to_markdown(self) -> str:
        """Generate markdown report"""
        lines = [
            "# Second Brain Lint Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        # Summary
        total_issues = (len(self.contradictions) + len(self.gaps) +
                       len(self.unsupported_claims) + len(self.stale_articles) +
                       len(self.broken_links))

        if total_issues == 0:
            lines.extend([
                "## ✅ All Clear!",
                "",
                "No issues detected. Your second brain is healthy.",
                ""
            ])
        else:
            lines.extend([
                "## Summary",
                "",
                f"- **Contradictions:** {len(self.contradictions)}",
                f"- **Gaps:** {len(self.gaps)}",
                f"- **Unsupported Claims:** {len(self.unsupported_claims)}",
                f"- **Stale Articles:** {len(self.stale_articles)}",
                f"- **Broken Links:** {len(self.broken_links)}",
                "",
            ])

        # Contradictions
        if self.contradictions:
            lines.extend([
                "## 🔴 Contradictions",
                "",
                "These wiki articles make conflicting statements:",
                ""
            ])
            for c in self.contradictions:
                lines.append(f"### {c['articles'][0]} ↔ {c['articles'][1]}")
                lines.append(f"**Topic:** {c['topic']}")
                lines.append(f"**Issue:** {c['issue']}")
                lines.append("")

        # Gaps
        if self.gaps:
            lines.extend([
                "## 🟡 Coverage Gaps",
                "",
                "These topics appear in raw sources but have no wiki article:",
                ""
            ])
            for g in self.gaps:
                lines.append(f"### {g['topic']}")
                lines.append(f"**Found in:** {len(g['raw_sources'])} raw files")
                lines.append(f"```")
                for src in g['raw_sources'][:3]:  # Show first 3
                    lines.append(f"- {src}")
                if len(g['raw_sources']) > 3:
                    lines.append(f"- ... and {len(g['raw_sources']) - 3} more")
                lines.append(f"```")
                lines.append("")

        # Unsupported claims
        if self.unsupported_claims:
            lines.extend([
                "## 🟠 Unsupported Claims",
                "",
                "These wiki statements may lack backing in raw sources:",
                ""
            ])
            for u in self.unsupported_claims:
                lines.append(f"### {u['article']}")
                lines.append(f"> {u['claim']}")
                lines.append("")

        # Stale articles
        if self.stale_articles:
            lines.extend([
                "## 🔵 Stale Articles",
                "",
                "These wiki articles haven't been updated despite new raw content:",
                ""
            ])
            for s in self.stale_articles:
                lines.append(f"### {s['article']}")
                lines.append(f"- Last modified: {s['last_modified']}")
                lines.append(f"- New raw files since: {s['new_raw_count']}")
                lines.append("")

        if self.broken_links:
            lines.extend([
                "## 🔗 Broken Wiki Links",
                "",
                "These links do not resolve to a wiki article:",
                "",
            ])
            for item in self.broken_links:
                lines.append(f"- `{item['article']}` → `[[{item['target']}]]`")
            lines.append("")

        # Suggestions
        if self.suggestions:
            lines.extend([
                "## 💡 Suggestions",
                ""
            ])
            for suggestion in self.suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")

        return "\n".join(lines)


def get_wiki_files() -> List[Path]:
    """Get all wiki markdown files except INDEX"""
    wiki_files = list(WIKI_DIR.glob("*.md"))
    return [f for f in wiki_files if f.name != "INDEX.md"]


def get_raw_files() -> List[Path]:
    """Get all raw markdown files"""
    return list(RAW_DIR.rglob("*.md"))


def extract_topics_from_raw() -> Dict[str, List[str]]:
    """Extract key topics/terms from raw files"""
    topic_files = defaultdict(list)

    # Simple heuristic: extract capitalized phrases and domain terms
    domain_terms = [
        "AI", "leadership", "cloud cost", "brief", "sprint", "org restructuring",
        "strike team", "agent ownership", "Databricks", "AWS", "culture interview",
        "EM", "engineering manager", "onboarding", "knowledge management",
        "second brain", "wiki", "Slack", "Confluence", "Jira"
    ]

    for raw_file in get_raw_files():
        try:
            content = raw_file.read_text(encoding='utf-8')
            rel_path = str(raw_file.relative_to(RAW_DIR))

            # Check for domain terms
            content_lower = content.lower()
            for term in domain_terms:
                if term.lower() in content_lower:
                    topic_files[term].append(rel_path)
        except Exception as e:
            print(f"Warning: Could not read {raw_file}: {e}")

    return topic_files


def check_gaps(report: LintReport):
    """Check for topics in raw/ that don't have wiki articles"""
    wiki_files = get_wiki_files()
    wiki_topics = {f.stem.replace('-', ' ').lower() for f in wiki_files}

    raw_topics = extract_topics_from_raw()

    for topic, sources in raw_topics.items():
        # Check if this topic has a wiki article
        topic_normalized = topic.lower()

        # Check for exact or partial matches
        has_coverage = False
        for wiki_topic in wiki_topics:
            if topic_normalized in wiki_topic or wiki_topic in topic_normalized:
                has_coverage = True
                break

        # If significant coverage (>3 raw files) but no wiki, report gap
        if not has_coverage and len(sources) >= 3:
            report.add_gap(topic, sources)


def check_freshness(report: LintReport):
    """Check if wiki articles are stale compared to raw sources"""
    wiki_files = get_wiki_files()

    for wiki_file in wiki_files:
        wiki_mtime = datetime.fromtimestamp(wiki_file.stat().st_mtime)

        # Count raw files modified after this wiki file
        new_raw_count = 0
        for raw_file in get_raw_files():
            raw_mtime = datetime.fromtimestamp(raw_file.stat().st_mtime)
            if raw_mtime > wiki_mtime:
                # Check if raw file is relevant (contains wiki topic keywords)
                topic_keywords = wiki_file.stem.replace('-', ' ').split()
                raw_content = raw_file.read_text(encoding='utf-8').lower()

                if any(kw.lower() in raw_content for kw in topic_keywords):
                    new_raw_count += 1

        # If wiki hasn't been updated in 30+ days AND there's new relevant raw content
        days_old = (datetime.now() - wiki_mtime).days
        if days_old > 30 and new_raw_count >= 2:
            report.add_stale_article(
                wiki_file.name,
                wiki_mtime.strftime('%Y-%m-%d'),
                new_raw_count
            )


def check_wiki_links(report: LintReport):
    """Check for broken wiki links"""
    wiki_files = get_wiki_files()
    existing_topics = {f.stem for f in wiki_files}

    for wiki_file in wiki_files:
        content = wiki_file.read_text(encoding='utf-8')

        # Find [[wiki-links]]
        links = re.findall(r'\[\[([^\]]+)\]\]', content)

        for link in links:
            target = link.split("|", 1)[0].split("#", 1)[0].strip()
            if target and target not in existing_topics:
                report.add_broken_link(wiki_file.name, target)


def run_quick_lint() -> LintReport:
    """Run structural checks without AI"""
    report = LintReport()

    print("🔍 Running quick lint...")
    print("  - Checking coverage gaps...")
    check_gaps(report)

    print("  - Checking freshness...")
    check_freshness(report)

    print("  - Checking wiki links...")
    check_wiki_links(report)

    return report


def run_full_lint_with_ai() -> LintReport:
    """Run full lint including AI-powered contradiction detection"""
    report = run_quick_lint()

    print("\n🤖 Running AI analysis...")
    print("  - Detecting contradictions...")

    # Use Claude to detect contradictions
    wiki_files = get_wiki_files()
    wiki_contents = {}

    for wiki_file in wiki_files:
        wiki_contents[wiki_file.name] = wiki_file.read_text(encoding='utf-8')

    # Prepare prompt for Claude
    prompt = """Analyze these wiki articles for contradictions, inconsistencies, or conflicting statements.

Focus on:
- Factual contradictions (one article says X, another says opposite)
- Timeline inconsistencies
- Conflicting recommendations or strategies
- Different interpretations of the same event

For each contradiction found, return JSON:
{"article1": "file1.md", "article2": "file2.md", "topic": "subject", "issue": "description"}

Wiki articles:

"""

    for name, content in wiki_contents.items():
        prompt += f"\n## {name}\n{content}\n"

    prompt += "\n\nReturn ONLY a JSON array of contradictions found, or empty array [] if none."

    # Call Claude via subprocess (assuming claude CLI is available)
    try:
        result = subprocess.run(
            ["claude", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            # Parse JSON response
            try:
                contradictions = json.loads(result.stdout.strip())
                for c in contradictions:
                    report.add_contradiction(
                        c['article1'],
                        c['article2'],
                        c['topic'],
                        c['issue']
                    )
            except json.JSONDecodeError:
                report.add_suggestion(
                    "AI contradiction detection completed but couldn't parse results. "
                    "Check manually for contradictions."
                )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        report.add_suggestion(
            "Could not run AI contradiction detection. Install claude CLI or run --quick mode."
        )

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Lint your second brain")
    parser.add_argument("--quick", action="store_true", help="Quick structural checks only (no AI)")
    parser.add_argument("--report", type=str, help="Save report to file")

    args = parser.parse_args()

    print("🧠 Second Brain Lint")
    print("=" * 50)

    # Check paths exist
    if not WIKI_DIR.exists():
        print(f"❌ Error: Wiki directory not found at {WIKI_DIR}")
        return 1

    if not RAW_DIR.exists():
        print(f"❌ Error: Raw directory not found at {RAW_DIR}")
        return 1

    # Run lint
    if args.quick:
        report = run_quick_lint()
    else:
        report = run_full_lint_with_ai()

    # Generate report
    markdown = report.to_markdown()

    # Save or print
    if args.report:
        OUTPUT_DIR.mkdir(exist_ok=True)
        report_path = OUTPUT_DIR / args.report
        report_path.write_text(markdown)
        print(f"\n📄 Report saved to: {report_path}")
    else:
        print("\n" + "=" * 50)
        print(markdown)

    # Exit code
    if report.has_issues():
        print(f"\n⚠️  Found {len(report.contradictions) + len(report.gaps) + len(report.unsupported_claims) + len(report.stale_articles) + len(report.broken_links)} issues")
        return 1
    else:
        print("\n✅ All clear!")
        return 0


if __name__ == "__main__":
    exit(main())
