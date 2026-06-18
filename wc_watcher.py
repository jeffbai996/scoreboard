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
EPHEMERAL_LIFESPAN = 30  # seconds — how long full-fidelity commentary posts stay up

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

def format_clock(clock_secs: float | None) -> str:
    """ESPN's own displayClock string is coarse (whole minutes, '90+14''
    stoppage notation). status.clock is raw elapsed seconds — convert that
    to mm:ss directly for a steadier per-poll readout."""
    if clock_secs is None:
        return ""
    total = int(clock_secs)
    return f"{total // 60:02d}:{total % 60:02d}"

# ESPN's position.abbreviation comes back as one of ~15 granular tags
# (CD-L, CM-R, AM, LB, ...) — too fine-grained to be readable at a glance
# for someone who doesn't already know soccer positions. Bucket down to
# the 4 lines a noob actually wants, ordered back-to-front.
POSITION_LINES = [
    ("G", "GK", "门将"),
    ("D", "DEF", "后卫"),
    ("M", "MID", "中场"),
    ("F", "FWD", "前锋"),
]

def _position_line(name: str) -> tuple[str, str] | None:
    # ESPN's position.name is a free-text label ("Center Left Defender",
    # "Left Back", "Attacking Midfielder Left", ...) — back/wing-back read
    # as defenders, attacking-mid reads as midfielder, so keyword match
    # rather than trusting any fixed prefix (abbreviations aren't
    # consistently prefixed: "LB"/"RB" are backs, not "L"/"R" anything).
    if not name:
        return None
    lower = name.lower()
    if "goalkeeper" in lower:
        return ("GK", "门将")
    if "defender" in lower or "back" in lower:
        return ("DEF", "后卫")
    if "midfielder" in lower:
        return ("MID", "中场")
    if "forward" in lower:
        return ("FWD", "前锋")
    return None

def format_lineups(summary: dict, home: str, away: str) -> str | None:
    rosters = summary.get("rosters", [])
    if not rosters:
        return None
    lines = ["Lineups 首发阵容"]
    for r in rosters:
        team_name = r.get("team", {}).get("displayName", "?")
        formation = r.get("formation", "")
        starters = sorted(
            (p for p in r.get("roster", []) if p.get("starter")),
            key=lambda p: int(p.get("formationPlace") or 0),
        )
        if not starters:
            continue
        header = f"{team_name}" + (f" ({formation})" if formation else "")
        lines.append(header)
        grouped: dict[str, list[str]] = {}
        for p in starters:
            name = p["athlete"].get("shortName", p["athlete"].get("displayName", "?"))
            bucket = _position_line(p.get("position", {}).get("name", ""))
            en, cn = bucket if bucket else ("?", "?")
            grouped.setdefault(f"{en} {cn}", []).append(name)
        for _, en, cn in POSITION_LINES:
            key = f"{en} {cn}"
            if key in grouped:
                lines.append(f"  {key}: {', '.join(grouped[key])}")
    return "\n".join(lines) if len(lines) > 1 else None

def format_commentary(text: str, minute: str) -> str:
    lower = text.lower()
    if "goal" in lower or "into the net" in lower or "opens the scoring" in lower:
        emoji = "⚽"
    elif "red card" in lower:
        emoji = "🟥"
    elif "yellow card" in lower:
        emoji = "🟨"
    elif "penalty" in lower or "var" in lower:
        emoji = "🎯"
    elif "attempt saved" in lower or "great save" in lower:
        emoji = "🧤"
    elif "substitut" in lower:
        emoji = "🔄"
    elif "injury" in lower:
        emoji = "🚑"
    else:
        emoji = "📋"
    return f"{emoji} [{minute}] {text}"

def scoreline(scores: dict, home: str, away: str) -> str:
    return f"{home} {scores.get(home, 0)} – {scores.get(away, 0)} {away}"

_STATUS_LABELS = {
    "STATUS_FIRST_HALF": ("1st half", "上半场"),
    "STATUS_HALFTIME": ("Half time", "半场休息"),
    "STATUS_SECOND_HALF": ("2nd half", "下半场"),
    "STATUS_IN_PROGRESS": ("In progress", "进行中"),
    "STATUS_FULL_TIME": ("Full time", "全场结束"),
    "STATUS_FINAL": ("Full time", "全场结束"),
    "STATUS_SCHEDULED": ("Scheduled", "未开始"),
}

def _render_board_lines(
    home: str, away: str, scores: dict, clock: str, status: str,
    goals: list, cards: list, stats: dict, recent: list | None,
    var_review: bool, lang: int,
) -> list[str]:
    """lang: 0 = English, 1 = Chinese. Labels split per Jeff 2026-06-17:
    one full code block per language rather than bilingual inline labels."""
    home_e, away_e = team_emoji(home), team_emoji(away)
    status_label = _STATUS_LABELS.get(status, (status, status))[lang]
    no_clock_states = ("STATUS_HALFTIME", "STATUS_FULL_TIME", "STATUS_FINAL")
    headers = {
        "var": ("⏳ VAR Review in progress", "⏳ VAR 审查中"),
        "goals": ("Goals", "进球"),
        "cards": ("Cards", "红黄牌"),
        "shots": ("Shots (on target)", "射门（射正）"),
        "poss": ("Possession", "控球"),
        "live": ("Live", "实时"),
    }
    lines = [
        f"{home_e} {home} {scores.get(home, 0)} - {scores.get(away, 0)} {away} {away_e}",
        f"{status_label}{f' ({clock})' if clock and status not in no_clock_states else ''}",
        "",
    ]
    if var_review:
        lines.append(headers["var"][lang])
        lines.append("")
    if goals:
        lines.append(headers["goals"][lang])
        for g in goals:
            tag = " (pen.)" if g["type"] == "pen." else " (OG)" if g["type"] == "OWN GOAL" else ""
            lines.append(f"⚽ {g['minute']} {g['player']} ({g['team']}){tag}")
        lines.append("")
    if cards:
        lines.append(headers["cards"][lang])
        for c in cards:
            emoji = "🟥" if c["type"] == "red" else "🟨"
            lines.append(f"{emoji} {c['minute']} {c['player']} ({c['team']})")
        lines.append("")
    if stats:
        h_stats = stats.get(home, {})
        a_stats = stats.get(away, {})
        lines.append(headers["shots"][lang])
        lines.append(
            f"{home}: {h_stats.get('totalShots', '?')} ({h_stats.get('shotsOnTarget', '?')})  |  "
            f"{away}: {a_stats.get('totalShots', '?')} ({a_stats.get('shotsOnTarget', '?')})"
        )
        lines.append(f"{headers['poss'][lang]}: {h_stats.get('possessionPct', '?')}% – {a_stats.get('possessionPct', '?')}%")
        lines.append("")
    if recent:
        lines.append(headers["live"][lang])
        for r in recent:
            lines.append(r)
    return lines

def render_scoreboard(
    home: str, away: str, scores: dict, clock: str, status: str,
    goals: list, cards: list, stats: dict, recent: list | None = None,
    var_review: bool = False,
) -> str:
    en = _render_board_lines(home, away, scores, clock, status, goals, cards, stats, recent, var_review, lang=0)
    cn = _render_board_lines(home, away, scores, clock, status, goals, cards, stats, recent, var_review, lang=1)
    return (
        "```\n" + "\n".join(en) + "\n```"
        + "\n```\n" + "\n".join(cn) + "\n```"
    )

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
    post_discord(channel_id, f"👀 加班鸭 live feed v7 — lineups grouped by position (bilingual), bilingual scoreboard labels, mm:ss clock, VAR review banner, persistent scoreboard (goals/cards permanent, full-fidelity commentary in the Live section, ~{EPHEMERAL_LIFESPAN}s rolling). Polling every {POLL_INTERVAL}s.")

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
    # Full-fidelity commentary (subs, injuries, dangerous chances, etc) — Jeff
    # 2026-06-17: show everything, but inside the scoreboard's "Live" section
    # instead of separate posts, so it updates silently via the same edit and
    # doesn't trigger notifications. (text, post_time) pairs, pruned by age.
    recent_commentary: list = []
    lineups_posted = False
    # ESPN's commentary feed doesn't give a clean "review resolved" signal,
    # just the initial "VAR Review" text — so treat any VAR mention as
    # opening a review window and let it auto-clear after VAR_REVIEW_TIMEOUT
    # instead of tracking actual resolution.
    var_review_until: float = 0.0
    VAR_REVIEW_TIMEOUT = 90  # seconds

    while True:
        event = fetch_scoreboard(event_id)
        if event is None:
            time.sleep(POLL_INTERVAL)
            continue

        comp = event["competitions"][0]
        status = comp["status"]
        current_state = status.get("type", {}).get("name", "")
        clock = format_clock(status.get("clock"))

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

        # Lineups post once at kickoff, as soon as ESPN exposes the rosters
        # (usually a few minutes before/at kickoff, not pregame).
        if not lineups_posted:
            lineups_text = format_lineups(summary, home_name, away_name)
            if lineups_text:
                post_discord(channel_id, f"```\n{lineups_text}\n```")
                lineups_posted = True
                scoreboard_buried_by += 1
            elif current_state not in ("STATUS_SCHEDULED", ""):
                # ESPN sometimes never exposes rosters for a match (data gap,
                # not a transient timing issue) — stop polling for it once
                # the game is actually underway so we're not wasting a fetch
                # every single poll for the rest of the match.
                lineups_posted = True

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
                # Goals/cards already get permanent posts from scoreboard
                # details above; log everything to the notebook either way.
                commentary_log.append({"minute": minute, "text": text})
                lower = text.lower()
                if "var" in lower:
                    var_review_until = time.monotonic() + VAR_REVIEW_TIMEOUT
                if "goal" in lower or "red card" in lower or "yellow card" in lower:
                    continue  # already posted permanently above
                # Jeff 2026-06-17: full fidelity for everything else (subs,
                # injuries, dangerous chances, saves, etc) — shown in the
                # scoreboard's "Live" section (edited, no notification) and
                # aged out after EPHEMERAL_LIFESPAN instead of separate posts.
                recent_commentary.append((format_commentary(text, minute), time.monotonic()))

        # Prune aged-out commentary lines
        now = time.monotonic()
        recent_commentary = [
            (txt, t) for txt, t in recent_commentary if now - t < EPHEMERAL_LIFESPAN
        ]
        var_review_active = now < var_review_until

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
            [txt for txt, _ in recent_commentary],
            var_review=var_review_active,
        )
        if scoreboard_msg_id is None:
            scoreboard_msg_id = post_discord(channel_id, board_text)
            polls_since_repost = 0
        elif scoreboard_buried_by > 0 or polls_since_repost >= REPOST_EVERY_POLLS:
            # Pinning used to handle "find it again," but each pin fires a
            # "X pinned a message" system notice every repost — Jeff 2026-06-17:
            # too noisy. Delete+repost alone already resurfaces it at the
            # bottom of the channel, which is the part that actually matters.
            delete_discord(channel_id, scoreboard_msg_id)
            scoreboard_msg_id = post_discord(channel_id, board_text)
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
