#!/usr/bin/env python3
"""
Print WC schedule for today + next N days.
Usage: python3 schedule.py [days_ahead]
  days_ahead: how many additional days beyond today (default 2)
"""
import sys
import requests
from datetime import datetime, timezone, timedelta, date

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

ET = timezone(timedelta(hours=-4))  # EDT (UTC-4, summer)
PT = timezone(timedelta(hours=-7))  # PDT (UTC-7, summer)

STATUS_LABELS = {
    "STATUS_SCHEDULED": "Scheduled",
    "STATUS_FIRST_HALF": "1st Half",
    "STATUS_HALFTIME": "Half Time",
    "STATUS_SECOND_HALF": "2nd Half",
    "STATUS_FULL_TIME": "FT",
    "STATUS_FINAL": "Final",
}

def fetch_day(d: date) -> list:
    date_str = d.strftime("%Y%m%d")
    try:
        r = requests.get(ESPN_SCOREBOARD, params={"dates": date_str}, timeout=10)
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as ex:
        print(f"  [fetch error for {date_str}: {ex}]")
        return []

def print_day(events: list, label: str) -> None:
    print(f"\n{'━' * 80}")
    print(f"  {label}")
    print(f"{'━' * 80}")
    if not events:
        print("  No fixtures found.")
        return
    print(f"  {'ID':<10} {'Match':<36} {'ET':>10} {'PT':>10}  {'Status':<16} Score")
    print(f"  {'-'*10} {'-'*36} {'-'*10} {'-'*10}  {'-'*16} -----")
    for e in events:
        c = e["competitions"][0]
        status_name = c["status"]["type"]["name"]
        detail = c["status"]["type"].get("detail", "")
        label_s = STATUS_LABELS.get(status_name, status_name)
        if status_name not in ("STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_SCHEDULED"):
            label_s = f"{label_s} {detail}".strip()

        kickoff_utc = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        et_str = kickoff_utc.astimezone(ET).strftime("%-I:%M%p ET")
        pt_str = kickoff_utc.astimezone(PT).strftime("%-I:%M%p PT")

        home, away = None, None
        scores = {}
        for c2 in c.get("competitors", []):
            name = c2["team"]["displayName"]
            scores[name] = c2.get("score", "-")
            if c2.get("homeAway") == "home":
                home = name
            else:
                away = name

        match_str = f"{home} vs {away}"
        score_str = (
            f"{scores.get(home,'-')} - {scores.get(away,'-')}"
            if status_name not in ("STATUS_SCHEDULED",)
            else ""
        )
        print(f"  {e['id']:<10} {match_str:<36} {et_str:>10} {pt_str:>10}  {label_s:<16} {score_str}")

def main() -> None:
    days_ahead = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    today_utc = datetime.now(timezone.utc).date()

    for offset in range(days_ahead + 1):
        day = today_utc + timedelta(days=offset)
        if offset == 0:
            day_label = f"TODAY — {day.strftime('%A, %B %-d')}"
        elif offset == 1:
            day_label = f"TOMORROW — {day.strftime('%A, %B %-d')}"
        else:
            day_label = day.strftime("%A, %B %-d")
        events = fetch_day(day)
        print_day(events, day_label)

    print()

if __name__ == "__main__":
    main()
