#!/usr/bin/env python3
"""
Schedule world-cup-watcher crons for a given set of ESPN game IDs.
Fetches kickoff times from ESPN, converts to server local time, and writes crontab entries.

Usage:
  python3 setup_crons.py <channel_id> <game_id> [<game_id> ...]

Example:
  python3 setup_crons.py YOUR_DISCORD_CHANNEL_ID 760490 760491 760492
"""
import json
import os
import sys
import subprocess
import requests
from datetime import datetime, timezone, timedelta

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
MINUTES_BEFORE = 5  # fire cron this many minutes before kickoff

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCH_SCRIPT = os.path.join(SCRIPT_DIR, "launch_watcher.sh")
# Snapshot of kickoff times AS USED to build the actual crontab. This is the
# ground truth once a cron is installed — ESPN's scoreboard API has been
# observed to return different kickoff data for the same game_id at different
# query times (2026-07-05: two manual checks both said England-Mexico kicked
# off 00:00Z/5pm PDT; the cron that actually got installed, from a separate
# run of this script, used 01:00Z/6pm — which was correct). Re-querying ESPN
# to "verify" an already-armed cron can therefore report a false discrepancy
# against stale/inconsistent upstream data. Always diff against THIS file,
# not a fresh API call, when checking whether an installed cron is right.
SCHEDULE_SNAPSHOT = os.path.join(SCRIPT_DIR, "schedule_snapshot.json")


def load_snapshot() -> dict:
    if os.path.exists(SCHEDULE_SNAPSHOT):
        with open(SCHEDULE_SNAPSHOT) as f:
            return json.load(f)
    return {}


def save_snapshot(snapshot: dict) -> None:
    with open(SCHEDULE_SNAPSHOT, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)


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

    snapshot = load_snapshot()
    new_entries = []
    for gid in game_ids:
        try:
            kickoff_utc, name = fetch_kickoff(gid)
            kickoff_local = kickoff_utc.astimezone(local_tz)
            fire_time = kickoff_local - timedelta(minutes=MINUTES_BEFORE)
            line = to_cron(fire_time, gid, channel_id)
            new_entries.append((gid, name, kickoff_local, fire_time, line))
            snapshot[str(gid)] = {
                "name": name,
                "kickoff_utc": kickoff_utc.isoformat(),
                "channel_id": channel_id,
                "cron_line": line,
                "set_at": datetime.now(timezone.utc).isoformat(),
            }
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
        save_snapshot(snapshot)
        print(f"Schedule snapshot written to {SCHEDULE_SNAPSHOT}")
    else:
        print("ERROR: crontab -  failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
