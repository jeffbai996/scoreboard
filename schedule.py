#!/usr/bin/env python3
"""
Print today's WC schedule with correct ET and PT kickoff times.
Usage: python3 schedule.py
"""
import requests
from datetime import datetime, timezone, timedelta

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

def main():
    r = requests.get(ESPN_SCOREBOARD, timeout=10)
    r.raise_for_status()
    events = r.json().get("events", [])

    print(f"{'ID':<10} {'Match':<40} {'ET':>8} {'PT':>8} {'Status':<15} {'Score'}")
    print("-" * 100)

    for e in events:
        c = e["competitions"][0]
        status_name = c["status"]["type"]["name"]
        detail = c["status"]["type"].get("detail", "")
        label = STATUS_LABELS.get(status_name, status_name)
        if status_name not in ("STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_SCHEDULED"):
            label = f"{label} {detail}"

        # Parse kickoff UTC
        kickoff_utc = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        et_str = kickoff_utc.astimezone(ET).strftime("%-I:%M%p ET")
        pt_str = kickoff_utc.astimezone(PT).strftime("%-I:%M%p PT")

        # Score
        scores = {c2["team"]["displayName"]: c2.get("score", "-") for c2 in c.get("competitors", [])}
        home, away = None, None
        for c2 in c.get("competitors", []):
            if c2.get("homeAway") == "home":
                home = c2["team"]["displayName"]
            else:
                away = c2["team"]["displayName"]
        score_str = f"{scores.get(home,'-')} - {scores.get(away,'-')}" if status_name not in ("STATUS_SCHEDULED",) else ""

        match_str = f"{home} vs {away}"
        print(f"{e['id']:<10} {match_str:<40} {et_str:>8} {pt_str:>8} {label:<15} {score_str}")

if __name__ == "__main__":
    main()
