#!/usr/bin/env bash
# Second Brain setup script.
# Run once from inside the second-brain-template directory:
#   bash setup.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"

echo "================================================"
echo "  Second Brain Setup"
echo "================================================"
echo ""
echo "Working directory: $ROOT"
echo ""

# --- Step 1: Python deps ---
echo "Step 1/6: Installing Python dependencies..."
"$PYTHON" -m pip install -q -r "$ROOT/Utils/ingest/requirements.txt"
"$PYTHON" -m pip install -q playwright
"$PYTHON" -m playwright install chromium --quiet
echo "  ✅ Done"

# --- Step 2: .env ---
echo ""
echo "Step 2/6: Configuring credentials..."
ENV_FILE="$ROOT/Utils/ingest/.env"

if [ -f "$ENV_FILE" ]; then
    echo "  .env already exists — skipping (delete it and re-run to reconfigure)"
else
    cp "$ROOT/Utils/ingest/.env.example" "$ENV_FILE"
    echo ""
    echo "  Enter your Anthropic API key (from https://console.anthropic.com):"
    read -r -p "  ANTHROPIC_API_KEY: " ANTHROPIC_KEY
    sed -i.bak "s|ANTHROPIC_API_KEY=|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"

    echo ""
    echo "  Do you want Slack ingestion? (y/n)"
    echo "  (Uses browser SSO — no API token needed)"
    read -r -p "  > " WANT_SLACK
    if [[ "$WANT_SLACK" == "y" || "$WANT_SLACK" == "Y" ]]; then
        echo ""
        echo "  Paste any Slack channel URL from your workspace:"
        echo "  (Open Slack in browser → right-click a channel → Copy link)"
        echo "  Format: https://app.slack.com/client/TXXXXXXXX/CXXXXXXXX"
        read -r -p "  SLACK_WORKSPACE_URL: " SLACK_WS_URL
        sed -i.bak "s|^SLACK_WORKSPACE_URL=|SLACK_WORKSPACE_URL=$SLACK_WS_URL|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"

        echo ""
        echo "  Which channel/DM do you want to sync daily?"
        echo "  Paste its URL (same format as above):"
        read -r -p "  Channel URL: " SLACK_CH_URL
        # Derive a short label from the URL (last path segment)
        SLACK_CH_LABEL=$(echo "$SLACK_CH_URL" | sed 's|.*/||' | cut -c1-20)
        SLACK_CH_JSON="{\"$SLACK_CH_LABEL\": \"$SLACK_CH_URL\"}"
        sed -i.bak "s|^SLACK_CHANNELS=|SLACK_CHANNELS=$SLACK_CH_JSON|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"

        SETUP_SLACK_LOGIN=1
    fi

    echo ""
    echo "  Do you want Confluence ingestion? (y/n)"
    read -r -p "  > " WANT_CONF
    if [[ "$WANT_CONF" == "y" || "$WANT_CONF" == "Y" ]]; then
        read -r -p "  CONFLUENCE_URL (e.g. https://myco.atlassian.net): " CONF_URL
        read -r -p "  CONFLUENCE_EMAIL: " CONF_EMAIL
        echo "  API token (from https://id.atlassian.com/manage-profile/security/api-tokens):"
        read -r -p "  CONFLUENCE_API_TOKEN: " CONF_TOKEN
        sed -i.bak "s|CONFLUENCE_URL=https://yourcompany.atlassian.net|CONFLUENCE_URL=$CONF_URL|" "$ENV_FILE"
        sed -i.bak "s|CONFLUENCE_EMAIL=you@yourcompany.com|CONFLUENCE_EMAIL=$CONF_EMAIL|" "$ENV_FILE"
        sed -i.bak "s|CONFLUENCE_API_TOKEN=|CONFLUENCE_API_TOKEN=$CONF_TOKEN|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
    fi
    echo "  ✅ .env created at $ENV_FILE"
fi

# --- Step 3: Claude Code hooks ---
echo ""
echo "Step 3/6: Configuring Claude Code hooks..."
SETTINGS_FILE="$ROOT/.claude/settings.local.json"

cat > "$SETTINGS_FILE" << ENDJSON
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $ROOT/hooks/session-start.py"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $ROOT/hooks/session-end.py"
          }
        ]
      }
    ]
  }
}
ENDJSON
echo "  ✅ Hooks configured at $SETTINGS_FILE"

# --- Step 4: Cron schedule ---
echo ""
echo "Step 4/6: Set up automatic ingestion schedule?"
echo "  This runs scheduler.py at 12:30 and 18:00 daily."
read -r -p "  Install cron jobs? (y/n): " WANT_CRON
if [[ "$WANT_CRON" == "y" || "$WANT_CRON" == "Y" ]]; then
    bash "$ROOT/Utils/ingest/install_cron.sh"
    echo "  ✅ Cron installed"
else
    echo "  Skipped. Run manually: bash $ROOT/Utils/ingest/install_cron.sh"
fi

# --- Step 5: Slack SSO login ---
echo ""
echo "Step 5/6: Slack SSO login"
if [[ "${SETUP_SLACK_LOGIN:-0}" == "1" ]]; then
    echo "  This will open a browser window for Slack SSO login."
    echo "  Log in once — the session is saved and reused automatically."
    read -r -p "  Open browser for Slack login now? (y/n): " DO_SLACK_LOGIN
    if [[ "$DO_SLACK_LOGIN" == "y" || "$DO_SLACK_LOGIN" == "Y" ]]; then
        cd "$ROOT/Utils/ingest" && "$PYTHON" ingest.py slack --login "$(grep '^SLACK_WORKSPACE_URL=' "$ENV_FILE" | cut -d= -f2-)"
        echo "  ✅ Slack session saved"
    else
        echo "  Skipped. Run later: cd $ROOT/Utils/ingest && python3 ingest.py slack --login <your-channel-url>"
    fi
else
    echo "  Skipped (Slack ingestion not configured)."
fi

# --- Step 6: Zoom login ---
echo ""
echo "Step 6/6: Set up Zoom Notes ingestion?"
echo "  This will open a browser for you to log in with SSO once."
echo "  After that, Zoom meeting notes sync automatically."
read -r -p "  Set up Zoom login now? (y/n): " WANT_ZOOM
if [[ "$WANT_ZOOM" == "y" || "$WANT_ZOOM" == "Y" ]]; then
    cd "$ROOT/Utils/ingest" && "$PYTHON" ingest.py zoom --login
    echo "  ✅ Zoom session saved"
else
    echo "  Skipped. Run later: cd $ROOT/Utils/ingest && python3 ingest.py zoom --login"
fi

# --- Done ---
echo ""
echo "================================================"
echo "  ✅ Setup complete!"
echo "================================================"
echo ""
echo "Open Claude Code in this directory to activate the wiki:"
echo "  cd $ROOT && claude"
echo ""
echo "Test ingestion manually:"
echo "  cd $ROOT/Utils/ingest"
echo "  python3 ingest.py zoom --days 7"
echo "  python3 ingest.py web <url>"
echo ""
echo "Add Slack/Confluence/web URLs to:"
echo "  $ROOT/Utils/ingest/source_urls.md"
echo ""
