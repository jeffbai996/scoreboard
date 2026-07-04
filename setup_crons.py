#!/usr/bin/env python3
"""
Schedule world-cup-watcher crons for a given set of ESPN game IDs.
Fetches kickoff times from ESPN, converts to server local time, and writes crontab entries.

Usage:
  python3 setup_crons.py <channel_id> <game_id> [<game_id> ...]

Example:
  python3 setup_crons.py YOUR_DISCORD_CHANNEL_ID 760490 760491 760492
"""
import os
import sys
import subprocess
import requests
from datetime import datetime, timezone, timedelta

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
MINUTES_BEFORE = 5  # fire cron this many minutes before kickoff

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCH_SCRIPT = os.path.join(SCRIPT_DIR, "launch_watcher.sh")


def fetch_kickoff(game_id: str) -> tuple[datetime, str]:
    """Return (kickoff_utc, match_name) for a given ESPN game ID."""
    # ESPN scoreboard date param isn't needed if we search a range; try today + 7 days
    now_utc = datetime.now(timezone.utc)
    for offset in range(10):
        day = (now_utc + timedelta(days=offset)).strftime("%Y%m%d")
        r = requests.get(ESPN_SCOREBOARD, params={"dates": day}, timeout=10)
        r.raise_for_status()
        for ev in r.json().get("events", []):
            if str(ev["id"]) == str(game_id):
                kickoff = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                return kickoff, ev["name"]
    raise ValueError(f"Game ID {game_id} not found in next 10 days")


def to_cron(dt_local: datetime, game_id: str, channel_id: str) -> str:
    """Build a crontab line for the given local datetime."""
    m = dt_local.minute
    h = dt_local.hour
    dom = dt_local.day
    mon = dt_local.month
    return f"{m} {h} {dom} {mon} * {LAUNCH_SCRIPT} {game_id} {channel_id}"


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    channel_id = sys.argv[1]
    game_ids = sys.argv[2:]

    # Detect server local timezone offset from UTC
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    local_offset = round((local_now - utc_now).total_seconds() / 3600)
    local_tz = timezone(timedelta(hours=local_offset))
    tz_name = f"UTC{local_offset:+d}" if local_offset != 0 else "UTC"
    print(f"Server local timezone detected: {tz_name}")

    new_entries = []
    for gid in game_ids:
        try:
            kickoff_utc, name = fetch_kickoff(gid)
            kickoff_local = kickoff_utc.astimezone(local_tz)
            fire_time = kickoff_local - timedelta(minutes=MINUTES_BEFORE)
            line = to_cron(fire_time, gid, channel_id)
            new_entries.append((gid, name, kickoff_local, fire_time, line))
            print(f"  {gid}  {name}")
            print(f"         kickoff: {kickoff_local.strftime('%Y-%m-%d %H:%M')} {tz_name}")
            print(f"         cron fires: {fire_time.strftime('%H:%M')} → {line}")
        except Exception as e:
            print(f"  {gid}  ERROR: {e}")

    if not new_entries:
        print("Nothing to schedule.")
        sys.exit(1)

    # Read existing crontab, strip any lines for these game IDs, append new ones
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    game_id_set = set(str(g) for g in game_ids)
    kept = [
        line for line in existing.splitlines()
        if not any(f"launch_watcher.sh {gid}" in line for gid in game_id_set)
    ]
    kept.append("")  # trailing newline
    for _, _, _, _, cron_line in new_entries:
        kept.append(cron_line)
    kept.append("")

    new_crontab = "\n".join(kept)
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    if proc.returncode == 0:
        print(f"\nCrontab updated. {len(new_entries)} game(s) scheduled.")
    else:
        print("ERROR: crontab -  failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
