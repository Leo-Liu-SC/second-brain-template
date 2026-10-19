#!/usr/bin/env bash
# Install a daily cron job that runs scheduler.py at 12:30 PM local time.
# Re-running this script is safe — it replaces the existing line.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
TAG="# second-brain-ingest"

mkdir -p "$SCRIPT_DIR/logs"

# Capture the user's full PATH so cron can find claude, python3, etc.
USER_PATH="$PATH"

# Read current crontab, removing any previous second-brain-ingest lines
existing=$(crontab -l 2>/dev/null | grep -v "$TAG" || true)

# Install both daily runs (12:30 and 18:00) with explicit PATH
{
  echo "$existing"
  echo "30 12 * * * PATH=$USER_PATH cd $SCRIPT_DIR && $PYTHON scheduler.py >> $SCRIPT_DIR/logs/cron.log 2>&1  $TAG"
  echo "0 18 * * * PATH=$USER_PATH cd $SCRIPT_DIR && $PYTHON scheduler.py >> $SCRIPT_DIR/logs/cron.log 2>&1  $TAG"
} | crontab -

echo "Installed cron job:"
crontab -l | grep "$TAG"
echo ""
echo "Log file: $SCRIPT_DIR/logs/cron.log"
echo "To remove: crontab -l | grep -v '$TAG' | crontab -"
