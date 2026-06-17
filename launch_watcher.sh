#!/bin/bash
# Launch wc_watcher with token from discord .env
TOKEN=$(grep DISCORD_BOT_TOKEN /home/jbai/.claude-alt/channels/discord/.env | cut -d= -f2)
EVENT_ID=${1:-760433}
CHANNEL_ID=${2:-1515472801983758348}
LOGFILE="/tmp/wc_watcher_${EVENT_ID}.log"

DISCORD_BOT_TOKEN="$TOKEN" python3 /home/jbai/repos/wc-watcher/wc_watcher.py "$EVENT_ID" "$CHANNEL_ID" >> "$LOGFILE" 2>&1
