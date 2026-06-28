#!/bin/bash
# Launch wc_watcher. Token resolution order:
#   1. DISCORD_BOT_TOKEN already in env
#   2. .env next to this script (gitignored)
#   3. WC_ENV_FILE if set (explicit override)
#   4. Hardcoded discord bot env path (cron fallback — no user env in cron)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN="$DISCORD_BOT_TOKEN"
if [ -z "$TOKEN" ] && [ -f "$SCRIPT_DIR/.env" ]; then
    TOKEN=$(grep DISCORD_BOT_TOKEN "$SCRIPT_DIR/.env" | cut -d= -f2)
fi
if [ -z "$TOKEN" ] && [ -n "$WC_ENV_FILE" ] && [ -f "$WC_ENV_FILE" ]; then
    TOKEN=$(grep DISCORD_BOT_TOKEN "$WC_ENV_FILE" | cut -d= -f2)
fi
_DEFAULT_ENV="$HOME/.claude-alt/channels/discord/.env"
if [ -z "$TOKEN" ] && [ -f "$_DEFAULT_ENV" ]; then
    TOKEN=$(grep DISCORD_BOT_TOKEN "$_DEFAULT_ENV" | cut -d= -f2)
fi
EVENT_ID=${1:?usage: launch_watcher.sh EVENT_ID CHANNEL_ID}
CHANNEL_ID=${2:?usage: launch_watcher.sh EVENT_ID CHANNEL_ID}
LOGFILE="/tmp/wc_watcher_${EVENT_ID}.log"

DISCORD_BOT_TOKEN="$TOKEN" python3 "$SCRIPT_DIR/wc_watcher.py" "$EVENT_ID" "$CHANNEL_ID" >> "$LOGFILE" 2>&1
