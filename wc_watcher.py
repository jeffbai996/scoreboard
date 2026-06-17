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

POLL_INTERVAL = 5  # seconds

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

def post_discord(channel_id: str, text: str) -> str | None:
    """Post a message, returning its message_id (None if posting failed
    or no bot token is configured) so callers can later edit it in place."""
    if not BOT_TOKEN:
        print(f"[DISCORD] {text}")
        return None
    r = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"content": text},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"Discord post failed: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("id")

def edit_discord(channel_id: str, message_id: str, text: str) -> bool:
    if not BOT_TOKEN:
        print(f"[DISCORD EDIT {message_id}] {text}")
        return True
    r = requests.patch(
        f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"content": text},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"Discord edit failed: {r.status_code} {r.text[:200]}")
        return False
    return True

def pin_discord(channel_id: str, message_id: str) -> bool:
    """Pin the scoreboard message so it stays reachable (Discord's pin tray)
    even though edits never bump it to the bottom of a busy channel."""
    if not BOT_TOKEN:
        return True
    r = requests.put(
        f"{DISCORD_API}/channels/{channel_id}/pins/{message_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(f"Discord pin failed: {r.status_code} {r.text[:200]}")
        return False
    return True

def delete_discord(channel_id: str, message_id: str) -> bool:
    if not BOT_TOKEN:
        return True
    r = requests.delete(
        f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(f"Discord delete failed: {r.status_code} {r.text[:200]}")
        return False
    return True

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

def render_scoreboard(
    home: str, away: str, scores: dict, clock: str, status: str,
    goals: list, cards: list, stats: dict,
) -> str:
    home_e, away_e = team_emoji(home), team_emoji(away)
    status_label = {
        "STATUS_FIRST_HALF": "1st half",
        "STATUS_HALFTIME": "Half time",
        "STATUS_SECOND_HALF": "2nd half",
        "STATUS_FULL_TIME": "Full time",
        "STATUS_FINAL": "Full time",
    }.get(status, status)
    lines = [
        f"{home_e} {home} {scores.get(home, 0)} - {scores.get(away, 0)} {away} {away_e}",
        f"{status_label}{f' ({clock})' if clock and 'HALF' not in status and 'FINAL' not in status else ''}",
        "",
    ]
    if goals:
        lines.append("Goals")
        for g in goals:
            tag = " (pen.)" if g["type"] == "pen." else " (OG)" if g["type"] == "OWN GOAL" else ""
            lines.append(f"⚽ {g['minute']} {g['player']} ({g['team']}){tag}")
        lines.append("")
    if cards:
        lines.append("Cards")
        for c in cards:
            emoji = "🟥" if c["type"] == "red" else "🟨"
            lines.append(f"{emoji} {c['minute']} {c['player']} ({c['team']})")
        lines.append("")
    if stats:
        h_stats = stats.get(home, {})
        a_stats = stats.get(away, {})
        lines.append("Shots (on target)")
        lines.append(
            f"{home}: {h_stats.get('totalShots', '?')} ({h_stats.get('shotsOnTarget', '?')})  |  "
            f"{away}: {a_stats.get('totalShots', '?')} ({a_stats.get('shotsOnTarget', '?')})"
        )
        lines.append(f"Possession: {h_stats.get('possessionPct', '?')}% – {a_stats.get('possessionPct', '?')}%")
    return "```\n" + "\n".join(lines) + "\n```"

def write_notebook(event_id: str, notebook: dict) -> None:
    path = f"/tmp/wc_notebook_{event_id}.json"
    try:
        with open(path, "w") as f:
            json.dump(notebook, f, indent=2)
    except Exception as ex:
        print(f"Notebook write error: {ex}")

def archive_notebook(event_id: str) -> None:
    """Move the live notebook to a persistent archive dir instead of deleting it,
    so completed-match notes survive past full time and stay queryable.
    Jeff 2026-06-17: keep these for the WC duration, delete the whole completed/
    dir once the tournament ends — not meant to be kept forever."""
    src = f"/tmp/wc_notebook_{event_id}.json"
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completed")
    os.makedirs(archive_dir, exist_ok=True)
    dst = os.path.join(archive_dir, f"wc_notebook_{event_id}.json")
    try:
        os.replace(src, dst)
    except Exception as ex:
        print(f"Notebook archive error: {ex}")

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
    post_discord(channel_id, f"👀 加班鸭 live feed v3 — persistent scoreboard + goals/cards/missed pens/injuries. Polling every {POLL_INTERVAL}s.")

    seen_commentary: set = set()
    seen_detail_uids: set = set()
    last_state = ""
    home_name = ""
    away_name = ""
    team_id_map: dict = {}
    commentary_log: list = []
    scoreboard_msg_id: str | None = None
    # Bumped every time something else gets posted to the channel — if it's
    # nonzero when we reach the scoreboard step, the old scoreboard message
    # is now buried under newer messages, so repost fresh instead of editing
    # in place (an edit never bumps a message back into view).
    scoreboard_buried_by = 0
    # The watcher can't see other people's chat messages burying the board,
    # so back that case with a flat timer — repost every ~2 min regardless.
    polls_since_repost = 0
    REPOST_EVERY_POLLS = max(1, round(120 / POLL_INTERVAL))

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

        # Key event details (goals/cards — always post these)
        details = comp.get("details", [])
        goals_list = [
            {
                "minute": d.get("clock", {}).get("displayValue", "?"),
                "player": (d.get("athletesInvolved") or [{}])[0].get("displayName", "?"),
                "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
                "type": ("OWN GOAL" if d.get("ownGoal") else "pen." if d.get("penaltyKick") else "goal"),
            }
            for d in details if d.get("scoringPlay")
        ]
        cards_list = [
            {
                "minute": d.get("clock", {}).get("displayValue", "?"),
                "player": (d.get("athletesInvolved") or [{}])[0].get("displayName", "?"),
                "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
                "type": ("red" if d.get("redCard") else "yellow"),
            }
            for d in details if d.get("redCard") or d.get("yellowCard")
        ]

        # State transitions
        if current_state != last_state:
            if current_state == "STATUS_HALFTIME":
                post_discord(channel_id, f"⏸️ **HALF TIME** | {scoreline(scores, home_name, away_name)}")
                scoreboard_buried_by += 1
            elif current_state in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                post_discord(channel_id, f"🏁 **FULL TIME** | {scoreline(scores, home_name, away_name)}")
                if scoreboard_msg_id:
                    final_board = render_scoreboard(
                        home_name, away_name, scores, clock, current_state,
                        goals_list, cards_list, {},
                    )
                    edit_discord(channel_id, scoreboard_msg_id, final_board)
                archive_notebook(event_id)
                break
            last_state = current_state
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
                # ESPN's competitors[].score lags the details feed by a poll or
                # two — the goal can land here before the score field ticks up,
                # so a goal posted from `scores` directly can show the pre-goal
                # tally. Count goals seen so far in `details` instead, which is
                # consistent with what just triggered this announcement.
                goals_so_far = {home_name: 0, away_name: 0}
                for d in details:
                    if d.get("scoringPlay"):
                        t = team_id_map.get(d.get("team", {}).get("id", ""), "")
                        if t in goals_so_far:
                            goals_so_far[t] += 1
                post_discord(channel_id,
                    f"⚽ **GOAL{own}{pk}!** {d_clock} — {player} {emoji}\n> {scoreline(goals_so_far, home_name, away_name)}")
                scoreboard_buried_by += 1
            elif detail.get("redCard"):
                post_discord(channel_id, f"🟥 **RED CARD** {d_clock} — {player} ({team_name})")
                scoreboard_buried_by += 1
            elif detail.get("yellowCard"):
                post_discord(channel_id, f"🟨 Yellow card {d_clock} — {player} ({team_name})")
                scoreboard_buried_by += 1

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
                # Goals/cards are already posted from scoreboard details above —
                # log commentary for the notebook only, never post it to Discord.
                # Jeff 2026-06-16: cut the offside/free-kick noise, goals only.
                # Jeff 2026-06-17: missed penalties + injuries are the exception —
                # no structured event type for these, so post straight from
                # commentary text instead of staying silent like other noise.
                commentary_log.append({"minute": minute, "text": text})
                lower = text.lower()
                if "penalty" in lower and ("miss" in lower or "saved" in lower):
                    post_discord(channel_id, f"🎯 **PENALTY MISSED** {minute} — {text}")
                    scoreboard_buried_by += 1
                elif "injury" in lower or "stretcher" in lower:
                    post_discord(channel_id, f"🚑 **INJURY** {minute} — {text}")
                    scoreboard_buried_by += 1

        # Persistent scoreboard — normally just edited in place every poll
        # (same approach as the agent view panel) so it doesn't spam the
        # channel. But an edit never bumps a message back into view, so if
        # something else just posted (goal/card/etc, tracked above) or the
        # repost timer's elapsed (covers plain chat burying it), delete the
        # old one and post a fresh copy so it actually resurfaces.
        polls_since_repost += 1
        board_text = render_scoreboard(
            home_name, away_name, scores, clock, current_state,
            goals_list, cards_list, stats,
        )
        if scoreboard_msg_id is None:
            scoreboard_msg_id = post_discord(channel_id, board_text)
            if scoreboard_msg_id:
                pin_discord(channel_id, scoreboard_msg_id)
            polls_since_repost = 0
        elif scoreboard_buried_by > 0 or polls_since_repost >= REPOST_EVERY_POLLS:
            delete_discord(channel_id, scoreboard_msg_id)
            scoreboard_msg_id = post_discord(channel_id, board_text)
            if scoreboard_msg_id:
                pin_discord(channel_id, scoreboard_msg_id)
            scoreboard_buried_by = 0
            polls_since_repost = 0
        else:
            edit_discord(channel_id, scoreboard_msg_id, board_text)

        # Update notebook every poll
        notebook = build_notebook(
            event_id, home_name, away_name, scores, clock,
            current_state, details, commentary_log, stats,
        )
        write_notebook(event_id, notebook)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
