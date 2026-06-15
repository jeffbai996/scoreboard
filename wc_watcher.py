#!/usr/bin/env python3
"""
Live WC match watcher — polls ESPN API, posts events to Discord thread.
Usage: python3 wc_watcher.py <event_id> <discord_channel_id>

Writes a live game notebook to /tmp/wc_notebook_<event_id>.json each poll,
so external queries can read current match state without re-hitting the API.
Notebook is deleted on FULL TIME.
"""
import sys
import time
import json
import requests
import os
from datetime import datetime, timezone

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
DISCORD_API = "https://discord.com/api/v10"
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

POLL_INTERVAL = 15  # seconds

KEY_COMMENTARY_PHRASES = [
    "goal", "penalty", "red card", "yellow card", "offside", "var",
    "attempt saved", "close range", "header", "free kick", "great save",
    "dangerous", "into the net", "opens the scoring", "equalise", "equaliz",
    "substitute", "substitution", "injury", "extra time", "stoppage time",
]

TEAM_EMOJIS = {
    "netherlands": "🇳🇱",
    "japan": "🇯🇵",
    "germany": "🇩🇪",
    "france": "🇫🇷",
    "brazil": "🇧🇷",
    "argentina": "🇦🇷",
    "spain": "🇪🇸",
    "england": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "portugal": "🇵🇹",
    "usa": "🇺🇸",
    "united states": "🇺🇸",
    "sweden": "🇸🇪",
    "tunisia": "🇹🇳",
    "ivory coast": "🇨🇮",
    "ecuador": "🇪🇨",
    "mexico": "🇲🇽",
    "south korea": "🇰🇷",
    "australia": "🇦🇺",
    "turkey": "🇹🇷",
    "türkiye": "🇹🇷",
    "curacao": "🇨🇼",
    "curaçao": "🇨🇼",
    "canada": "🇨🇦",
    "switzerland": "🇨🇭",
    "qatar": "🇶🇦",
    "belgium": "🇧🇪",
    "saudi arabia": "🇸🇦",
    "uruguay": "🇺🇾",
    "iran": "🇮🇷",
    "senegal": "🇸🇳",
    "norway": "🇳🇴",
}

def team_emoji(name: str) -> str:
    lower = name.lower()
    for k, v in TEAM_EMOJIS.items():
        if k in lower:
            return v
    return "⚽"

def post_discord(channel_id: str, text: str) -> None:
    if not BOT_TOKEN:
        print(f"[DISCORD] {text}")
        return
    r = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"content": text},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"Discord post failed: {r.status_code} {r.text[:200]}")

def fetch_scoreboard(event_id: str) -> dict | None:
    try:
        r = requests.get(ESPN_SCOREBOARD, timeout=10)
        r.raise_for_status()
        for e in r.json().get("events", []):
            if e["id"] == event_id:
                return e
    except Exception as ex:
        print(f"Scoreboard fetch error: {ex}")
    return None

def fetch_summary(event_id: str) -> dict:
    try:
        r = requests.get(ESPN_SUMMARY, params={"event": event_id}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        print(f"Summary fetch error: {ex}")
    return {}

def is_key_moment(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in KEY_COMMENTARY_PHRASES)

def format_commentary(text: str, minute: str) -> str:
    lower = text.lower()
    if "goal" in lower or "into the net" in lower or "opens the scoring" in lower:
        return f"⚽ **[{minute}]** {text}"
    elif "red card" in lower:
        return f"🟥 **[{minute}]** {text}"
    elif "yellow card" in lower:
        return f"🟨 **[{minute}]** {text}"
    elif "penalty" in lower or "var" in lower:
        return f"🎯 **[{minute}]** {text}"
    elif "attempt saved" in lower or "great save" in lower:
        return f"🧤 **[{minute}]** {text}"
    elif "substitut" in lower:
        return f"🔄 **[{minute}]** {text}"
    elif "injury" in lower:
        return f"🚑 **[{minute}]** {text}"
    else:
        return f"📋 **[{minute}]** {text}"

def scoreline(scores: dict, home: str, away: str) -> str:
    return f"{home} {scores.get(home, 0)} – {scores.get(away, 0)} {away}"

def write_notebook(event_id: str, notebook: dict) -> None:
    path = f"/tmp/wc_notebook_{event_id}.json"
    try:
        with open(path, "w") as f:
            json.dump(notebook, f, indent=2)
    except Exception as ex:
        print(f"Notebook write error: {ex}")

def delete_notebook(event_id: str) -> None:
    path = f"/tmp/wc_notebook_{event_id}.json"
    try:
        os.remove(path)
    except Exception:
        pass

def build_notebook(
    event_id: str,
    home: str,
    away: str,
    scores: dict,
    clock: str,
    status: str,
    details: list,
    commentary_log: list,
    stats: dict,
) -> dict:
    goals = [
        {
            "minute": d.get("clock", {}).get("displayValue", "?"),
            "player": (d.get("athletesInvolved") or [{}])[0].get("displayName", "?"),
            "team": d.get("team", {}).get("displayName", "?"),
            "type": ("OWN GOAL" if d.get("ownGoal") else "pen." if d.get("penaltyKick") else "goal"),
        }
        for d in details if d.get("scoringPlay")
    ]
    cards = [
        {
            "minute": d.get("clock", {}).get("displayValue", "?"),
            "player": (d.get("athletesInvolved") or [{}])[0].get("displayName", "?"),
            "team": d.get("team", {}).get("displayName", "?"),
            "type": ("red" if d.get("redCard") else "yellow"),
        }
        for d in details if d.get("redCard") or d.get("yellowCard")
    ]
    return {
        "event_id": event_id,
        "updated": datetime.now(timezone.utc).isoformat(),
        "match": f"{home} vs {away}",
        "score": {home: scores.get(home, 0), away: scores.get(away, 0)},
        "clock": clock,
        "status": status,
        "goals": goals,
        "cards": cards,
        "stats": stats,
        "key_commentary": commentary_log[-30:],  # last 30 key moments
    }

def main():
    if len(sys.argv) < 3:
        print("Usage: wc_watcher.py <espn_event_id> <discord_channel_id>")
        sys.exit(1)

    event_id = sys.argv[1]
    channel_id = sys.argv[2]

    print(f"Watching event {event_id} → Discord {channel_id}")
    post_discord(channel_id, f"👀 加班鸭 live feed v2 — key moments + goals + cards + subs. Polling every {POLL_INTERVAL}s.")

    seen_commentary: set = set()
    seen_detail_uids: set = set()
    last_state = ""
    home_name = ""
    away_name = ""
    team_id_map: dict = {}
    commentary_log: list = []

    while True:
        event = fetch_scoreboard(event_id)
        if event is None:
            time.sleep(POLL_INTERVAL)
            continue

        comp = event["competitions"][0]
        status = comp["status"]
        current_state = status.get("type", {}).get("name", "")
        clock = status.get("type", {}).get("detail", "")

        # Build team map once
        if not home_name:
            for c in comp.get("competitors", []):
                name = c["team"]["displayName"]
                tid = c["team"]["id"]
                team_id_map[tid] = name
                if c.get("homeAway") == "home":
                    home_name = name
                else:
                    away_name = name

        # Scores
        scores: dict = {}
        for c in comp.get("competitors", []):
            scores[c["team"]["displayName"]] = int(c.get("score", 0))

        # State transitions
        if current_state != last_state:
            if current_state == "STATUS_HALFTIME":
                post_discord(channel_id, f"⏸️ **HALF TIME** | {scoreline(scores, home_name, away_name)}")
            elif current_state in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                post_discord(channel_id, f"🏁 **FULL TIME** | {scoreline(scores, home_name, away_name)}")
                delete_notebook(event_id)
                break
            last_state = current_state

        # Key event details (goals/cards — always post these)
        details = comp.get("details", [])
        for detail in details:
            athletes = detail.get("athletesInvolved", [])
            player = athletes[0].get("displayName", "Unknown") if athletes else "Unknown"
            uid = (
                detail.get("clock", {}).get("displayValue", ""),
                detail.get("type", {}).get("text", ""),
                athletes[0].get("id", "") if athletes else "",
            )
            if uid in seen_detail_uids:
                continue
            seen_detail_uids.add(uid)

            d_clock = detail.get("clock", {}).get("displayValue", "?'")
            team_name = team_id_map.get(detail.get("team", {}).get("id", ""), "")
            emoji = team_emoji(team_name)

            if detail.get("scoringPlay"):
                own = " (OWN GOAL)" if detail.get("ownGoal") else ""
                pk = " (pen.)" if detail.get("penaltyKick") else ""
                post_discord(channel_id,
                    f"⚽ **GOAL{own}{pk}!** {d_clock} — {player} {emoji}\n> {scoreline(scores, home_name, away_name)}")
            elif detail.get("redCard"):
                post_discord(channel_id, f"🟥 **RED CARD** {d_clock} — {player} ({team_name})")
            elif detail.get("yellowCard"):
                post_discord(channel_id, f"🟨 Yellow card {d_clock} — {player} ({team_name})")

        # Commentary + stats
        summary = fetch_summary(event_id)
        commentary = summary.get("commentary", [])

        # Extract stats if available
        stats: dict = {}
        for box in summary.get("boxscore", {}).get("teams", []):
            tname = box.get("team", {}).get("displayName", "?")
            stat_map = {s["name"]: s.get("displayValue", "?") for s in box.get("statistics", [])}
            stats[tname] = stat_map

        for item in commentary:
            seq = item.get("sequence", -1)
            text = item.get("text", "")
            minute = item.get("time", {}).get("displayValue", "")
            uid = (seq, text[:40])
            if uid in seen_commentary:
                continue
            seen_commentary.add(uid)
            if seq == 0:
                continue
            if is_key_moment(text):
                commentary_log.append({"minute": minute, "text": text})
                post_discord(channel_id, format_commentary(text, minute))

        # Update notebook every poll
        notebook = build_notebook(
            event_id, home_name, away_name, scores, clock,
            current_state, details, commentary_log, stats,
        )
        write_notebook(event_id, notebook)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
