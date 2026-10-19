# Second Brain Lint System

Health checking for your knowledge base to catch errors before they compound.

## What It Detects

### 🔴 Contradictions
Wiki articles that make conflicting statements about the same topic.

**Example:** One article says "AI PRs are at 30%" while another says "AI PRs are at 20%"

**Risk:** Editorial trap - baked-in errors that persist across queries.

### 🟡 Coverage Gaps
Topics that appear frequently in raw/ but have no wiki article.

**Example:** "Databricks" mentioned in 4+ raw files but no wiki article exists.

**Risk:** Missing synthesis - you're dumping knowledge but not organizing it.

### 🟠 Unsupported Claims *(requires full AI mode)*
Wiki statements that may not be backed by raw sources.

**Example:** Wiki says "Team velocity increased 40%" but no raw source contains this data.

**Risk:** Made-up facts from AI hallucination during wiki synthesis.

### 🔵 Stale Articles
Wiki files that haven't been updated despite new relevant raw content.

**Example:** `ai-leadership.md` last modified May 1, but 5 new meetings about AI leadership added in June.

**Risk:** Outdated knowledge - your wiki becomes less accurate over time.

## Usage

### Quick Mode (Structural Checks Only)
Fast, no AI required. Checks gaps, freshness, and broken links.

```bash
cd ~/AIDev/mytools/ingest
python3 lint.py --quick
```

**Runtime:** ~5 seconds
**When to use:** Weekly quick health check

### Full Mode (AI-Powered Analysis)
Includes contradiction detection using Claude AI.

```bash
python3 lint.py
```

**Runtime:** ~60-90 seconds
**When to use:** Monthly deep health check

### Save Report to File

```bash
python3 lint.py --report lint-report-$(date +%Y-%m-%d).md
```

Report saved to: `~/AIDev/second-brain/outputs/lint-report-YYYY-MM-DD.md`

## Integration with Scheduler

### Option 1: Manual Monthly Run
Add to your calendar:
- First Monday of each month
- Run: `cd ~/AIDev/mytools/ingest && python3 lint.py --report monthly-lint.md`
- Review the report and fix issues

### Option 2: Automated via Cron
Add weekly quick lints to your cron:

```bash
# Add to crontab (crontab -e)
# Every Monday at 9 AM
0 9 * * 1 cd ~/AIDev/mytools/ingest && python3 lint.py --quick --report weekly-lint.md
```

### Option 3: Add to scheduler.py
Modify `scheduler.py` to run lint after wiki updates:

```python
# At the end of scheduler.py main()
print("\n🔍 Running lint check...")
subprocess.run(["python3", "lint.py", "--quick"])
```

## Interpreting Results

### Exit Codes
- `0` = All clear, no issues
- `1` = Issues found

### Priority Guide

| Finding | Priority | Action |
|---------|----------|--------|
| **Contradictions** | 🔴 HIGH | Fix immediately - these corrupt your knowledge base |
| **Stale articles (30+ days)** | 🟠 MEDIUM | Update wiki with new raw content |
| **Coverage gaps (5+ mentions)** | 🟡 MEDIUM | Create wiki articles for important topics |
| **Coverage gaps (3-4 mentions)** | 🔵 LOW | Monitor, may not need wiki article yet |
| **Broken links** | 🟡 MEDIUM | Fix links or create missing articles |

## Example Workflow

### Monthly Health Check

1. **Run full lint:**
   ```bash
   cd ~/AIDev/mytools/ingest
   python3 lint.py --report monthly-$(date +%Y-%m).md
   ```

2. **Review report:**
   ```bash
   cat ~/AIDev/second-brain/outputs/monthly-$(date +%Y-%m).md
   ```

3. **Fix contradictions first** (if any):
   - Read both wiki articles
   - Check raw sources
   - Decide which is correct
   - Update wiki manually or ask Claude Code to fix

4. **Address stale articles:**
   - Run scheduler to update wiki from new raw content:
     ```bash
     python3 scheduler.py
     ```

5. **Create missing wiki articles** (for important gaps):
   - Ask Claude Code: "Create a wiki article about [topic] based on raw sources"
   - Or add to `source_urls.md` if you need to ingest external content first

6. **Re-run lint** to verify fixes:
   ```bash
   python3 lint.py --quick
   ```

## Customization

Edit `lint.py` to adjust:

### Gap Detection Threshold
Line 158: Change `len(sources) >= 3` to higher/lower threshold

```python
# Report gaps only if topic appears in 5+ files
if not has_coverage and len(sources) >= 5:
```

### Freshness Threshold
Line 179: Change `days_old > 30` and `new_raw_count >= 2`

```python
# Report stale if 60+ days old and 5+ new relevant files
if days_old > 60 and new_raw_count >= 5:
```

### Domain Terms (for gap detection)
Line 107-113: Add your specific domain terms

```python
domain_terms = [
    "AI", "leadership", "cloud cost", "brief", "sprint",
    # Add your terms:
    "your-product-name", "your-team-name", "your-initiative"
]
```

## Troubleshooting

### "Could not run AI contradiction detection"
**Cause:** `claude` CLI not found or not in PATH

**Fix:**
1. Run in `--quick` mode (no AI needed), OR
2. Install claude CLI (if available), OR
3. Comment out AI section in `lint.py` (lines 237-268)

### "Warning: Could not read [file]"
**Cause:** Encoding issues or corrupted file

**Fix:** Check the file manually and fix encoding or remove if corrupted

### False Positives in Gaps
**Cause:** Lint detects mentions of common words like "wiki" or "Slack"

**Fix:**
- These may not need articles (they're tool names, not topics)
- Edit domain_terms list to exclude these
- Or just ignore in the report

## Related

- See `CLAUDE.md` in second-brain for wiki maintenance rules
- See `scheduler.py` for daily ingestion process
- See `second-brain-framework.md` wiki article for architecture
