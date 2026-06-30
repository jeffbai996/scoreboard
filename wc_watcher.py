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
import re
import signal
import unicodedata
import requests
import os
from datetime import datetime, timezone, timedelta

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
DISCORD_API = "https://discord.com/api/v10"
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Same fixed summer offsets schedule.py uses, so kickoff times in the match
# intro match what schedule lookups already show.
_ET = timezone(timedelta(hours=-4))  # EDT (UTC-4, summer)
_PT = timezone(timedelta(hours=-7))  # PDT (UTC-7, summer)

POLL_INTERVAL = 5  # seconds
EPHEMERAL_LIFESPAN = 60  # seconds — how long full-fidelity commentary posts stay up
DISCORD_CONTENT_LIMIT = 2000

KEY_COMMENTARY_PHRASES = [
    "goal", "penalty", "red card", "yellow card", "offside", "var",
    "attempt saved", "close range", "header", "free kick", "great save",
    "dangerous", "into the net", "opens the scoring", "equalise", "equaliz",
    "substitute", "substitution", "injury", "extra time", "stoppage time",
]

# ESPN's commentary feed has no structured "injury" flag (unlike scoringPlay/
# redCard/yellowCard on detail entries) — a sub forced by injury only shows up
# as this exact text pattern. Matched against the same commentary stream that
# feeds recent_commentary, so a player who actually leaves the pitch gets a
# permanent board entry instead of aging out after EPHEMERAL_LIFESPAN like a
# routine sub would.
_INJURY_SUB_RE = re.compile(
    r"^Substitution,\s*(?P<team>[^.]+)\.\s*(?P<incoming>.+?) replaces (?P<outgoing>.+?) because of an injury\.?\s*$"
)

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
    "algeria": "🇩🇿",
    "austria": "🇦🇹",
    "bosnia-herzegovina": "🇧🇦",
    "cape verde": "🇨🇻",
    "colombia": "🇨🇴",
    "congo dr": "🇨🇩",
    "croatia": "🇭🇷",
    "czechia": "🇨🇿",
    "egypt": "🇪🇬",
    "ghana": "🇬🇭",
    "haiti": "🇭🇹",
    "iraq": "🇮🇶",
    "jordan": "🇯🇴",
    "morocco": "🇲🇦",
    "new zealand": "🇳🇿",
    "panama": "🇵🇦",
    "paraguay": "🇵🇾",
    "scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "south africa": "🇿🇦",
    "uzbekistan": "🇺🇿",
}

def team_emoji(name: str) -> str:
    lower = name.lower()
    for k, v in TEAM_EMOJIS.items():
        if k in lower:
            return v
    return "⚽"

# Full 2026 WC qualified-nation list — pulled from ESPN's scoreboard across
# the whole tournament window, keyed on the exact displayName string ESPN
# returns (not lowercased/fuzzy — team names are a closed set so an exact
# dict lookup is more reliable than substring matching).
TEAM_NAMES_CN = {
    "Algeria": "阿尔及利亚", "Argentina": "阿根廷", "Australia": "澳大利亚",
    "Austria": "奥地利", "Belgium": "比利时", "Bosnia-Herzegovina": "波黑",
    "Brazil": "巴西", "Canada": "加拿大", "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚", "Congo DR": "刚果民主共和国", "Croatia": "克罗地亚",
    "Curaçao": "库拉索", "Czechia": "捷克", "Ecuador": "厄瓜多尔",
    "Egypt": "埃及", "England": "英格兰", "France": "法国", "Germany": "德国",
    "Ghana": "加纳", "Haiti": "海地", "Iran": "伊朗", "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦", "Japan": "日本", "Jordan": "约旦",
    "Mexico": "墨西哥", "Morocco": "摩洛哥", "Netherlands": "荷兰",
    "New Zealand": "新西兰", "Norway": "挪威", "Panama": "巴拿马",
    "Paraguay": "巴拉圭", "Portugal": "葡萄牙", "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特阿拉伯", "Scotland": "苏格兰", "Senegal": "塞内加尔",
    "South Africa": "南非", "South Korea": "韩国", "Spain": "西班牙",
    "Sweden": "瑞典", "Switzerland": "瑞士", "Tunisia": "突尼斯",
    "Türkiye": "土耳其", "United States": "美国", "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
}

# Short names used ONLY in the scoreboard header line to prevent flush-left
# rendering: when a full "🏴 Team vs Team 🏴" line fills BOARD_WIDTH exactly,
# _center() adds zero padding so the title slams to both edges while the
# shorter score/clock lines are indented — looks misaligned. Full names stay
# everywhere else (goals, cards, stats, etc.).
TEAM_NAMES_SHORT = {
    "United States": "USA",
    "Bosnia-Herzegovina": "Bosnia",
    "Saudi Arabia": "S. Arabia",
    "South Africa": "S. Africa",
    "South Korea": "S. Korea",
    "New Zealand": "N. Zealand",
}
TEAM_NAMES_SHORT_CN = {
    "Congo DR": "刚果金",   # 刚果民主共和国 (12w) → 刚果金 (6w); Algeria matchup hits 32 otherwise
}

def team_name(name: str, lang: int) -> str:
    """lang: 0 = English (passthrough), 1 = Chinese (mapped, falls back
    to the English name if a team isn't in TEAM_NAMES_CN yet)."""
    if lang == 0:
        return name
    return TEAM_NAMES_CN.get(name, name)

def _fit_to_discord_limit(text: str, limit: int = DISCORD_CONTENT_LIMIT) -> str:
    """Discord rejects oversized content outright (400 BASE_TYPE_MAX_LENGTH),
    which silently freezes the live board mid-match since the post/edit just
    fails — truncate instead, re-closing the code fence if the board uses one."""
    if len(text) <= limit:
        return text
    if text.startswith("```") and text.endswith("```"):
        closing = "\n…\n```"
        return text[: limit - len(closing)] + closing
    return text[: limit - 1] + "…"

def post_discord(channel_id: str, text: str) -> str | None:
    """Post a message, returning its message_id (None if posting failed
    or no bot token is configured) so callers can later edit it in place."""
    text = _fit_to_discord_limit(text)
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
    text = _fit_to_discord_limit(text)
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
    # ESPN's unscoped /scoreboard call is anchored to its own "today" cutoff,
    # which can still lag behind UTC by most of a day — a match that's
    # already on tomorrow's UTC date (e.g. a midnight-ET kickoff) silently
    # disappears from the unscoped response. Query an explicit 3-day window
    # around now instead of trusting ESPN's default.
    today = datetime.now(timezone.utc).date()
    date_range = f"{(today - timedelta(days=1)):%Y%m%d}-{(today + timedelta(days=1)):%Y%m%d}"
    try:
        r = requests.get(ESPN_SCOREBOARD, params={"dates": date_range}, timeout=10)
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

def _fmt_schedule_detail(detail: str) -> str:
    """Reformat ESPN's 'Thu, June 25th at 7:00 PM EDT' → 'Thu, Jun. 25 @ 7:00 PM ET'.
    Falls back to the raw string if the pattern doesn't match."""
    import re
    m = re.match(
        r"(\w{3}),\s+(\w+)\s+(\d+)(?:st|nd|rd|th)\s+at\s+(.+?)\s+ED?T",
        detail, re.IGNORECASE
    )
    if not m:
        return detail
    day, month, date_num, time_part = m.groups()
    month_abbr = month[:3] + "."
    return f"{day}, {month_abbr} {date_num} @ {time_part} ET"


def _fmt_kickoff(date_str: str) -> tuple[str, str]:
    """ESPN dates come back UTC ('2026-06-19T19:00Z') — render ET/PT like
    schedule.py does, so the intro matches what schedule lookups already show."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return ("?", "?")
    et = dt.astimezone(_ET)
    pt = dt.astimezone(_PT)
    return (et.strftime("%-I:%M%p ET"), pt.strftime("%-I:%M%p PT"))

def _standings_lines(summary: dict, home: str, away: str, lang: int) -> list[str]:
    """Group table snapshot, so the intro shows what's at stake — not just
    who's playing. Only the group containing home/away is relevant here."""
    groups = summary.get("standings", {}).get("groups", [])
    group = next(
        (g for g in groups if any(
            e.get("team") in (home, away) for e in g.get("standings", {}).get("entries", [])
        )),
        None,
    )
    if not group:
        return []
    label = "STANDINGS" if lang == 0 else "小组积分"
    entries = group["standings"]["entries"]
    rows = []
    for e in entries:
        stats = {s["name"]: s["displayValue"] for s in e.get("stats", [])}
        rows.append((
            stats.get("rank", "?"),
            team_name(e.get("team", "?"), lang),
            stats.get("overall", "?"),
            stats.get("pointDifferential", "?"),
            stats.get("points", "?"),
        ))
    # Pad the name column to the longest entry so GD/Pts line up regardless
    # of "Sweden" vs "United States" — _divider()'s width alone can't do this.
    name_width = max(_display_width(name) for _, name, _, _, _ in rows)
    lines = ["", label, _divider()]
    for rank, name, overall, gd, pts in rows:
        pad = " " * (name_width - _display_width(name))
        lines.append(f"{rank}. {name}{pad}  {overall}  GD {gd:>3}  Pts {pts:>2}")
    return lines

def _h2h_lines(summary: dict, home: str, away: str, lang: int) -> list[str]:
    """Last meeting between these two teams, if ESPN has one on file."""
    h2h = summary.get("headToHeadGames", [])
    home_entry = next((t for t in h2h if t.get("team", {}).get("displayName") == home), None)
    if not home_entry or not home_entry.get("events"):
        return []
    last = home_entry["events"][0]
    when = last.get("gameDate", "")[:10]
    score = last.get("score", "?")
    result_team = home if last.get("homeTeamId") == home_entry["team"]["id"] else away
    label = "LAST MEETING" if lang == 0 else "上次交锋"
    home_disp, away_disp = team_name(home, lang), team_name(away, lang)
    return ["", label, _divider(), f"{when}  {home_disp} {score} {away_disp}"]

_FORM_RESULT_EMOJI = {"W": "🟩", "D": "🟨", "L": "🟥"}

def _recent_form_lines(summary: dict, home: str, away: str, lang: int) -> list[str]:
    """Last 5 results for each side (W/L/D), so the intro shows form coming
    into the match, not just the standings snapshot. Color squares instead of
    ANSI codes since those render fine on Discord mobile, unlike escape codes."""
    blocks = summary.get("lastFiveGames", [])
    if not blocks:
        return []
    rows = []
    for block in blocks:
        tname = block.get("team", {}).get("displayName", "?")
        if tname not in (home, away):
            continue
        events = block.get("events", [])
        if not events:
            continue
        results = " ".join(_FORM_RESULT_EMOJI.get(e.get("gameResult", ""), "⬜") for e in events)
        rows.append((tname, team_name(tname, lang), results))
    if not rows:
        return []
    # Pad the name column to the longer of the two team names so the squares
    # start at the same column regardless of "Sweden" vs "Netherlands".
    name_width = max(_display_width(disp) for _, disp, _ in rows)
    label = "RECENT FORM" if lang == 0 else "近期状态"
    lines = ["", label, _divider()]
    for tname, disp, results in rows:
        pad = " " * (name_width - _display_width(disp))
        lines.append(f"{team_emoji(tname)} {disp}{pad}  {results}")
    return lines

def _leaders_lines(summary: dict, lang: int) -> list[str]:
    """Live top performer per stat category per team — who's actually
    standing out so far, not just the box score totals."""
    leaders = summary.get("leaders", [])
    if not leaders:
        return []
    label = "STANDOUTS" if lang == 0 else "焦点表现"
    lines = ["", label, _divider()]
    # One stat per line instead of a single "·"-joined line — the joined version
    # wraps badly on a phone-width Discord client once a team has 4-5 categories.
    for i, team_block in enumerate(leaders):
        tname = team_block.get("team", {}).get("displayName", "?")
        disp = team_name(tname, lang)
        picks = []
        for cat in team_block.get("leaders", []):
            top = cat.get("leaders", [{}])[0] if cat.get("leaders") else None
            if not top:
                continue
            last_name = top.get("athlete", {}).get("lastName")
            athlete_name = last_name if last_name and last_name != "null" else top.get("athlete", {}).get("fullName", "?")
            display_value = top.get("displayValue", "?")
            # Goals/Assists categories report "Matches: N, Goals: N" instead of a bare
            # count early in the tournament — pull just the trailing number out of it.
            match = re.search(r":\s*(\d+)\s*$", display_value)
            if match:
                display_value = match.group(1)
            picks.append(f"{cat.get('displayName', '?')}: {athlete_name} ({display_value})")
        if not picks:
            continue
        if i > 0:
            lines.append("")
        lines.append(f"{team_emoji(tname)} {disp}")
        lines.extend(f"  {pick}" for pick in picks)
    return lines if len(lines) > 3 else []

def format_match_intro(scoreboard_comp: dict, summary: dict, home: str, away: str) -> str | None:
    """One-time fixture block at kickoff: venue/city, kickoff time, round,
    broadcast, referee, group standings, last meeting, then a formation-
    grouped visual lineup (jersey numbers, not just names) for both teams,
    plus live standout-performer stats. English only — the live scoreboard
    is where EN/CN alternation happens. Returns None if ESPN hasn't exposed
    rosters yet."""
    rosters = summary.get("rosters", [])
    if not rosters:
        return None

    venue = scoreboard_comp.get("venue", {})
    venue_name = venue.get("fullName", "")
    city = venue.get("address", {}).get("city", "")
    round_note = scoreboard_comp.get("altGameNote", "")
    kickoff_et, kickoff_pt = _fmt_kickoff(scoreboard_comp.get("date", ""))
    broadcast_names = [n for b in scoreboard_comp.get("broadcasts", []) for n in b.get("names", [])]
    officials = summary.get("gameInfo", {}).get("officials", [])
    referee = next((o["displayName"] for o in officials if o.get("position", {}).get("name") == "Referee"), "")

    lang = 0
    home_disp, away_disp = team_name(home, lang), team_name(away, lang)
    home_e, away_e = team_emoji(home), team_emoji(away)
    lines = [
        _divider("═"),
        _center(f"{home_e} {home_disp} vs {away_disp} {away_e}"),
        _divider("═"),
        "",
    ]
    if round_note:
        lines.append(round_note)
    if venue_name:
        lines.append(f"📍 {venue_name}" + (f", {city}" if city else ""))
    if kickoff_et != "?":
        lines.append(f"🕐 {kickoff_et} / {kickoff_pt}")
    if broadcast_names:
        lines.append(f"📺 {', '.join(broadcast_names)}")
    if referee:
        lines.append(f"🟨 Referee: {referee}")

    lines.extend(_standings_lines(summary, home, away, lang))
    lines.extend(_recent_form_lines(summary, home, away, lang))
    lines.extend(_h2h_lines(summary, home, away, lang))

    for r in rosters:
        r_team = r.get("team", {}).get("displayName", "?")
        formation = r.get("formation", "")
        starters = sorted(
            (p for p in r.get("roster", []) if p.get("starter")),
            key=lambda p: int(p.get("formationPlace") or 0),
        )
        if not starters:
            continue
        lines.append("")
        disp = team_name(r_team, lang)
        header = f"{team_emoji(r_team)} {disp}" + (f" ({formation})" if formation else "")
        lines.append(header)
        lines.append(_divider())
        grouped: dict[str, list[str]] = {}
        for p in starters:
            name = p["athlete"].get("shortName", p["athlete"].get("displayName", "?"))
            jersey = p.get("jersey", "")
            tag = f"#{jersey} {name}" if jersey else name
            bucket = _position_line(p.get("position", {}).get("name", ""))
            en, cn = bucket if bucket else ("?", "?")
            grouped.setdefault(f"{en} {cn}", []).append(tag)
        for _, en, cn in POSITION_LINES:
            key = f"{en} {cn}"
            if key in grouped:
                lines.append(f"  {en}  {' · '.join(grouped[key])}")
    lines.extend(_leaders_lines(summary, lang))
    return "```\n" + "\n".join(lines) + "\n```"

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
    "STATUS_END_OF_REGULATION": ("ET", "加时赛"),
    "STATUS_EXTRA_TIME": ("Extra time", "加时赛"),
    "STATUS_OVER_TIME": ("Extra time", "加时赛"),
    "STATUS_OVERTIME": ("Extra time", "加时赛"),          # ESPN variant (no underscore between OVER TIME)
    "STATUS_EXTRA_TIME_HALF": ("ET half time", "加时半场"),
    "STATUS_HALFTIME_ET": ("ET half time", "加时半场"),
    "STATUS_HALFTIME_EXTRA_TIME": ("ET half time", "加时半场"),
    "STATUS_SHOOTOUT": ("Penalties", "点球大战"),
    "STATUS_PENALTY": ("Penalties", "点球大战"),
    "STATUS_PENALTY_KICKS": ("Penalties", "点球大战"),    # ESPN variant
    "STATUS_FINAL_PEN": ("Final (pens)", "点球决出"),    # post-match final-pen result
    # AET end states — ESPN emits descriptive text ("AET", "AET-Pens") in the
    # detail field for these, not a clock. Map them so the fallback never fires.
    "STATUS_END_OF_EXTRA_TIME": ("Full time (AET)", "加时结束"),
    "STATUS_AET": ("Full time (AET)", "加时结束"),
    "STATUS_AET_PENS": ("Penalties", "点球大战"),    # AET → pens transition state
    "STATUS_SCHEDULED": ("Sched.", "未开始"),
    "STATUS_DELAYED": ("Delayed", "延期"),
    "STATUS_SUSPENDED": ("Suspended", "中断"),
    "STATUS_POSTPONED": ("Postponed", "推迟"),
    "STATUS_ABANDONED": ("Abandoned", "终止"),
}

# Statuses where play has stopped for a reason worth surfacing (weather,
# drinks break, etc) — ESPN gives no dedicated reason field, just a
# start-delay/end-delay commentary play type with free text. Tracked
# separately from goals/cards/injuries since it's a single current value
# (cleared on resume), not an accumulating list.
DELAY_STATUSES = ("STATUS_DELAYED", "STATUS_SUSPENDED", "STATUS_POSTPONED")

# Fixed width tuned for mobile Discord code blocks — wide tables wrap
# ugly on a phone screen, so everything below builds to fit this.
BOARD_WIDTH = 32

def _divider(ch: str = "─") -> str:
    return ch * BOARD_WIDTH

# str.center() counts codepoints, not terminal cells — CJK characters and
# flag emoji (regional-indicator pairs, or England/Scotland's multi-codepoint
# tag sequences) all render ~2 cells wide in a monospace Discord code block,
# so naive centering on len() skews noticeably once flags/Chinese enter the
# string. Walk the string collapsing each flag sequence to one "wide" unit
# and call unicodedata on everything else.
_REGIONAL_INDICATOR = re.compile("[\U0001F1E6-\U0001F1FF]")
_TAG_FLAG = re.compile("\U0001F3F4[\U000E0000-\U000E007F]+")

def _display_width(text: str) -> int:
    # Pull out tag-sequence flags (England/Scotland/Wales) first since they
    # span many codepoints but are exactly one 2-wide glyph.
    consumed = set()
    width = 0
    for m in _TAG_FLAG.finditer(text):
        width += 2
        consumed.update(range(m.start(), m.end()))
    i = 0
    while i < len(text):
        if i in consumed:
            i += 1
            continue
        ch = text[i]
        if _REGIONAL_INDICATOR.match(ch) and i + 1 < len(text) and _REGIONAL_INDICATOR.match(text[i + 1]):
            width += 2  # a regional-indicator pair = one flag glyph
            i += 2
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        i += 1
    return width

def _center(text: str) -> str:
    pad = BOARD_WIDTH - _display_width(text)
    if pad <= 0:
        return text
    left = pad // 2
    return " " * left + text + " " * (pad - left)

def _pct_to_str(frac) -> str:
    # ESPN's passPct/shotPct/etc come back as a 0-1 fraction (0.9), not a
    # whole percentage like possessionPct (51.7) — needs its own scaling.
    try:
        return f"{float(frac) * 100:.0f}%"
    except (TypeError, ValueError):
        return "?"

def _ratio_pct_str(accurate, total) -> str:
    # ESPN's passPct displayValue is pre-rounded to one decimal (0.7, 0.9) --
    # coarse enough that it can sit pinned on the same value for most of a
    # match even as the underlying accurate/total counts keep climbing
    # (caught 2026-06-17: looked frozen most of the 2nd half).
    # Compute from the raw counts instead for real precision.
    try:
        t = float(total)
        if t == 0:
            return "?"
        return f"{float(accurate) / t * 100:.0f}%"
    except (TypeError, ValueError):
        return "?"

def _possession_bar(home_pct: str, away_pct: str) -> str:
    try:
        h = float(home_pct)
    except (TypeError, ValueError):
        return ""
    bar_width = BOARD_WIDTH - 2
    filled = round(bar_width * h / 100)
    return "[" + "█" * filled + "░" * (bar_width - filled) + "]"

PENALTY_STATES = (
    "STATUS_SHOOTOUT", "STATUS_PENALTY", "STATUS_PENALTY_KICKS",
    "STATUS_FINAL_PEN",
)


def _pen_board_lines(pen_shots: list, home: str, away: str, lang: int) -> list[str]:
    """Render an X/O shootout grid for the scoreboard.

    pen_shots is the `summary.shootout` list:
      [{"team": "Germany", "shots": [{"shotNumber": 1, "player": "...", "didScore": bool}, ...]}, ...]

    Works for any number of rounds (handles sudden death beyond 5).
    """
    if not pen_shots:
        return []

    label = "PENALTIES" if lang == 0 else "点球大战"
    by_team: dict[str, list[dict]] = {}
    for block in pen_shots:
        by_team[block["team"]] = block.get("shots", [])

    # Determine number of rounds taken so far
    max_rounds = max((len(shots) for shots in by_team.values()), default=0)
    if max_rounds == 0:
        return []

    # Score counts
    home_scored = sum(1 for s in by_team.get(home, []) if s.get("didScore"))
    away_scored = sum(1 for s in by_team.get(away, []) if s.get("didScore"))

    lines = ["", label, _divider()]
    lines.append(_center(f"{home_scored} – {away_scored}"))
    lines.append("")

    home_e, away_e = team_emoji(home), team_emoji(away)
    home_disp = team_name(home, lang)
    away_disp = team_name(away, lang)

    for team_disp, team_key, emoji in (
        (home_disp, home, home_e),
        (away_disp, away, away_e),
    ):
        shots = by_team.get(team_key, [])
        dots = []
        for sh in shots:
            if sh.get("didScore"):
                dots.append("⬤")   # scored
            else:
                dots.append("✕")   # missed/saved
        # Pad with · for rounds the other team has taken that this team hasn't yet
        while len(dots) < max_rounds:
            dots.append("·")

        row = " ".join(dots)
        # Name + dots on one line; player names on the next indented
        lines.append(f"{emoji} {team_disp}")
        lines.append(f"   {row}")
        for sh in shots:
            mark = "⬤" if sh.get("didScore") else "✕"
            player_last = sh.get("player", "?").split()[-1]
            lines.append(f"   {sh['shotNumber']}. {player_last} {mark}")
        lines.append("")

    # Remove trailing blank
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_board_lines(
    home: str, away: str, scores: dict, clock: str, status: str,
    goals: list, cards: list, stats: dict, recent: list | None,
    var_review: bool, lang: int, injuries: list | None = None,
    delay_reason: str = "", pen_shots: list | None = None,
) -> list[str]:
    """lang: 0 = English, 1 = Chinese. Labels split 2026-06-17:
    one full code block per language rather than bilingual inline labels."""
    home_disp, away_disp = team_name(home, lang), team_name(away, lang)
    home_e, away_e = team_emoji(home), team_emoji(away)
    _short = TEAM_NAMES_SHORT_CN if lang == 1 else TEAM_NAMES_SHORT
    home_h = _short.get(home, home_disp)
    away_h = _short.get(away, away_disp)
    _fallback = status.replace("STATUS_", "").replace("_", " ").title() if status else "?"
    status_label = _STATUS_LABELS.get(status, (_fallback, _fallback))[lang]
    no_clock_states = (
        "STATUS_HALFTIME", "STATUS_FULL_TIME", "STATUS_FINAL",
        "STATUS_FINAL_PEN",
        # ESPN's detail field for these statuses is descriptive text ("Penalties",
        # "AET", "FT-Pens"), not a clock — don't show it as a clock.
        "STATUS_SHOOTOUT", "STATUS_PENALTY", "STATUS_PENALTY_KICKS",
        "STATUS_END_OF_REGULATION",
        "STATUS_END_OF_EXTRA_TIME", "STATUS_AET", "STATUS_AET_PENS",
    )
    headers = {
        "var": ("⏳ VAR REVIEW", "⏳ VAR 审查中"),
        "delay": ("⏸️ MATCH STOPPED", "⏸️ 比赛暂停"),
        "goals": ("GOALS", "进球"),
        "injuries": ("INJURIES", "伤退"),
        "cards": ("CARDS", "红黄牌"),
        "shots": ("SHOTS (ON TARGET)", "射门（射正）"),
        "poss": ("POSSESSION", "控球"),
        "extra": ("MATCH STATS", "比赛数据"),
        "live": ("LIVE", "实时"),
    }

    h_score = scores.get(home, 0)
    a_score = scores.get(away, 0)
    if status in PENALTY_STATES and pen_shots:
        # Show regulation score + penalty tally: e.g. "1(4) – 1(3)"
        home_pen = sum(1 for b in pen_shots for s in b.get("shots", [])
                       if b["team"] == home and s.get("didScore"))
        away_pen = sum(1 for b in pen_shots for s in b.get("shots", [])
                       if b["team"] == away and s.get("didScore"))
        score_line = f"{h_score}({home_pen}) – {a_score}({away_pen})"
    else:
        score_line = f"{h_score} - {a_score}"
    clock_str = f" {clock}" if clock and status not in no_clock_states else ""
    lines = [
        _divider("═"),
        _center(f"{home_e} {home_h} vs {away_h} {away_e}"),
        _center(score_line),
        _center(f"· {status_label}{clock_str} ·"),
        _divider("═"),
    ]
    if status in PENALTY_STATES and pen_shots:
        lines.extend(_pen_board_lines(pen_shots, home, away, lang))
    if status in DELAY_STATUSES:
        lines.append("")
        lines.append(_center(headers["delay"][lang]))
        if delay_reason:
            # Reason text comes from ESPN's commentary feed as free-form
            # English ("Delay in match for a drinks break.", weather notes,
            # etc) — no translated variant exists, so it's shown as-is in
            # both languages rather than mistranslating or dropping it.
            lines.append(_center(delay_reason))
    if var_review:
        lines.append("")
        lines.append(_center(headers["var"][lang]))
    if goals:
        lines.append("")
        lines.append(headers["goals"][lang])
        lines.append(_divider())
        for g in goals:
            tag = " (pen.)" if g["type"] == "pen." else " (OG)" if g["type"] == "OWN GOAL" else ""
            lines.append(f"⚽ {g['minute']} {g['player']} ({team_name(g['team'], lang)}){tag}")
    if cards:
        lines.append("")
        lines.append(headers["cards"][lang])
        lines.append(_divider())
        for c in cards:
            emoji = "🟥" if c["type"] == "red" else "🟨"
            lines.append(f"{emoji} {c['minute']} {c['player']} ({team_name(c['team'], lang)})")
    if injuries:
        lines.append("")
        lines.append(headers["injuries"][lang])
        lines.append(_divider())
        for inj in injuries:
            lines.append(f"🚑 {inj['minute']} {inj['player']} ({team_name(inj['team'], lang)})")
    if stats:
        h_stats = stats.get(home, {})
        a_stats = stats.get(away, {})
        lines.append("")
        lines.append(headers["shots"][lang])
        lines.append(_divider())
        lines.append(
            f"{home_disp}: {h_stats.get('totalShots', '?')} ({h_stats.get('shotsOnTarget', '?')})"
        )
        lines.append(
            f"{away_disp}: {a_stats.get('totalShots', '?')} ({a_stats.get('shotsOnTarget', '?')})"
        )
        lines.append("")
        h_poss, a_poss = h_stats.get("possessionPct", "?"), a_stats.get("possessionPct", "?")
        lines.append(f"{headers['poss'][lang]}  {h_poss}% – {a_poss}%")
        bar = _possession_bar(h_poss, a_poss)
        if bar:
            lines.append(bar)
        lines.append("")
        lines.append(headers["extra"][lang])
        lines.append(_divider())
        h_pass_pct = _ratio_pct_str(h_stats.get("accuratePasses"), h_stats.get("totalPasses"))
        a_pass_pct = _ratio_pct_str(a_stats.get("accuratePasses"), a_stats.get("totalPasses"))
        pass_label = "Pass acc." if lang == 0 else "传球成功率"
        corners_label = "Corners" if lang == 0 else "角球"
        fouls_label = "Fouls" if lang == 0 else "犯规"
        lines.append(f"{pass_label}  {h_pass_pct} – {a_pass_pct}")
        lines.append(
            f"{corners_label}  {h_stats.get('wonCorners', '?')} – {a_stats.get('wonCorners', '?')}"
        )
        lines.append(
            f"{fouls_label}  {h_stats.get('foulsCommitted', '?')} – {a_stats.get('foulsCommitted', '?')}"
        )
    if recent:
        lines.append("")
        lines.append(headers["live"][lang])
        lines.append(_divider())
        for r in recent:
            lines.append(r)
    return lines

SCOREBOARD_LANG_SWITCH_SECONDS = 30  # how long each language stays up before swapping

def current_scoreboard_lang(start_time: float) -> int:
    """0 = English, 1 = Chinese — alternates on a fixed wall-clock interval
    since match start, independent of poll cadence, so the displayed
    language is deterministic even if polls get delayed."""
    elapsed = time.monotonic() - start_time
    return int(elapsed // SCOREBOARD_LANG_SWITCH_SECONDS) % 2

def render_scoreboard(
    home: str, away: str, scores: dict, clock: str, status: str,
    goals: list, cards: list, stats: dict, recent: list | None = None,
    var_review: bool = False, lang: int = 0, injuries: list | None = None,
    delay_reason: str = "", pen_shots: list | None = None,
) -> str:
    lines = _render_board_lines(home, away, scores, clock, status, goals, cards, stats, recent, var_review, lang, injuries, delay_reason, pen_shots)
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
    Keep these for the WC duration, delete the whole completed/
    dir once the tournament ends — not meant to be kept forever."""
    src = f"/tmp/wc_notebook_{event_id}.json"
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completed")
    os.makedirs(archive_dir, exist_ok=True)
    dst = os.path.join(archive_dir, f"wc_notebook_{event_id}.json")
    try:
        os.replace(src, dst)
    except Exception as ex:
        print(f"Notebook archive error: {ex}")

def cleanup_match_messages(event_id: str, channel_id: str) -> int:
    """Delete every Discord message a previous run of this watcher posted —
    scoreboard posts/reposts and goal/card/injury/half/full-time
    announcements — and clear the live notebook, so a restart or a
    deliberate cancel doesn't strand stale messages in the channel.

    Deliberately leaves the intro card (and its /tmp/wc_intro_posted_<id>
    marker) alone: that's often posted hours ahead of kickoff, sometimes
    manually, and isn't part of what gets duplicated/stranded on a
    restart — only the live-tracking messages are.

    Returns how many messages were deleted. No-op (returns 0) if there's no
    live notebook for this match — a finished, archived match is a
    deliberate end-state, not something to clean up."""
    path = f"/tmp/wc_notebook_{event_id}.json"
    try:
        with open(path) as f:
            notebook = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    ids = notebook.get("posted_message_ids", [])
    deleted = 0
    for mid in ids:
        if delete_discord(channel_id, mid):
            deleted += 1
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return deleted

def _first_participant(detail: dict) -> dict:
    # ESPN's scoreboard endpoint (/scoreboard, what the live poll loop reads)
    # and the summary endpoint (/summary?event=, header.competitions[0])
    # describe the same "details" concept with two different shapes:
    #   scoreboard: athletesInvolved -> [{"id": ..., "displayName": ...}]   (flat)
    #   summary:    participants     -> [{"athlete": {"id": ..., "displayName": ...}}]
    # Handle both rather than assuming one — first entry is the scorer/carder,
    # any later entries are assists.
    participants = detail.get("participants") or []
    if participants:
        return participants[0].get("athlete", {}) or {}
    athletes = detail.get("athletesInvolved") or []
    if athletes:
        return athletes[0] or {}
    return {}

def _scorer_name(detail: dict) -> str:
    return _first_participant(detail).get("displayName", "?")

def _participant_athlete_id(detail: dict) -> str:
    return _first_participant(detail).get("id", "")

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
    team_id_map: dict,
    injuries: list | None = None,
    delay_reason: str = "",
    posted_message_ids: list | None = None,
) -> dict:
    # The scoreboard endpoint's detail entries only carry team.id, not
    # team.displayName -- needs the id->name map built from competitors,
    # same as the other two goal/card builders in the poll loop use.
    goals = [
        {
            "minute": d.get("clock", {}).get("displayValue", "?"),
            "player": _scorer_name(d),
            "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
            "type": ("OWN GOAL" if d.get("ownGoal") else "pen." if d.get("penaltyKick") else "goal"),
        }
        for d in details if d.get("scoringPlay")
    ]
    cards = [
        {
            "minute": d.get("clock", {}).get("displayValue", "?"),
            "player": _scorer_name(d),
            "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
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
        "injuries": injuries or [],
        "delay_reason": delay_reason,
        # Every message this watcher has posted to the match channel —
        # scoreboard posts/reposts, intro card, goal/card/half/full-time
        # announcements. Lets a relaunch (restart or deliberate cancel)
        # clean up everything from the previous run instead of stranding it.
        "posted_message_ids": posted_message_ids or [],
        "stats": stats,
        "key_commentary": commentary_log[-30:],  # last 30 key moments
    }

def main():
    if len(sys.argv) < 3:
        print("Usage: wc_watcher.py <espn_event_id> <discord_channel_id> [--cleanup-only]")
        sys.exit(1)

    event_id = sys.argv[1]
    channel_id = sys.argv[2]

    if "--cleanup-only" in sys.argv[3:]:
        # Deliberate cancel, not a relaunch — wipe this match's messages and
        # exit instead of starting a poll loop.
        n = cleanup_match_messages(event_id, channel_id)
        print(f"Cleaned up {n} message(s) for event {event_id}, no watch started.")
        return

    # A relaunch (process restart, or manually re-running for the same
    # event_id) used to leave the previous run's scoreboard/announcements
    # stranded in the channel, plus reposting duplicates once dedup state
    # reset. Wipe anything a prior run left behind before starting fresh —
    # no-ops cleanly if this is genuinely the first launch (no notebook yet).
    n = cleanup_match_messages(event_id, channel_id)
    if n:
        print(f"Cleared {n} stranded message(s) from a previous run before starting.")

    print(f"Watching event {event_id} → Discord {channel_id}")

    watcher_start = time.monotonic()
    seen_commentary: set = set()
    seen_detail_uids: set = set()
    last_state = ""
    announced_states: set = set()  # states whose transition message has already been posted
    home_name = ""
    away_name = ""
    team_id_map: dict = {}
    commentary_log: list = []
    injuries: list = []  # persistent, like goals/cards — see _INJURY_SUB_RE
    delay_reason: str = ""  # current stoppage reason, if any — see DELAY_STATUSES
    posted_ids: list = []  # everything posted this run — see cleanup_match_messages

    def _post(text: str) -> str | None:
        # Tracked variant of post_discord for anything cleanup_match_messages
        # should be able to remove on a future restart/cancel. The intro card
        # is deliberately posted via the untracked post_discord directly (see
        # below) since it survives restarts on purpose.
        mid = post_discord(channel_id, text)
        if mid:
            posted_ids.append(mid)
        return mid

    pen_shots: list = []        # shootout data from summary; populated once penalties start
    _pen_announced: set = set() # (team, shotNumber) tuples we've already posted
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
    # Full-fidelity commentary (subs, injuries, dangerous chances, etc) —
    # 2026-06-17: show everything, but inside the scoreboard's "Live" section
    # instead of separate posts, so it updates silently via the same edit and
    # doesn't trigger notifications. (text, post_time) pairs, pruned by age.
    recent_commentary: list = []
    # Persisted across process restarts (unlike the rest of this function's
    # in-memory state) so a manually-posted preview intro doesn't get
    # duplicated when the watcher actually starts polling at kickoff.
    intro_marker = f"/tmp/wc_intro_posted_{event_id}"
    lineups_posted = os.path.exists(intro_marker)
    # ESPN's commentary feed doesn't give a clean "review resolved" signal,
    # just the initial "VAR Review" text — so treat any VAR mention as
    # opening a review window and let it auto-clear after VAR_REVIEW_TIMEOUT
    # instead of tracking actual resolution.
    var_review_until: float = 0.0
    VAR_REVIEW_TIMEOUT = 90  # seconds

    # ESPN sometimes gets stuck in STATUS_SECOND_HALF without ever transitioning
    # to STATUS_FULL_TIME/STATUS_FINAL (observed: Colombia vs Portugal ran 5+ hrs
    # post-game). If status + score freeze for this long while in 2nd half or
    # extra time, treat it as game over and force a clean shutdown.
    FROZEN_STATE_TIMEOUT = 40 * 60  # 40 minutes — bumped to cover AET (2x15 min + breaks)
    _frozen_state_key: tuple = ()
    _frozen_state_since: float = 0.0
    IN_PROGRESS_STATES = (
        "STATUS_FIRST_HALF", "STATUS_SECOND_HALF",
        "STATUS_IN_PROGRESS", "STATUS_HALFTIME",
        "STATUS_END_OF_REGULATION",
        "STATUS_EXTRA_TIME", "STATUS_OVER_TIME", "STATUS_OVERTIME",
        "STATUS_EXTRA_TIME_HALF", "STATUS_HALFTIME_ET", "STATUS_HALFTIME_EXTRA_TIME",
        "STATUS_SHOOTOUT", "STATUS_PENALTY", "STATUS_PENALTY_KICKS",
        "STATUS_END_OF_EXTRA_TIME", "STATUS_AET", "STATUS_AET_PENS",
    )
    FINAL_STATES = ("STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_FINAL_PEN")
    POST_90_STATES = (
        "STATUS_SECOND_HALF", "STATUS_IN_PROGRESS",
        "STATUS_END_OF_REGULATION",
        "STATUS_EXTRA_TIME", "STATUS_OVER_TIME", "STATUS_OVERTIME",
        "STATUS_EXTRA_TIME_HALF", "STATUS_HALFTIME_EXTRA_TIME",
        "STATUS_SHOOTOUT", "STATUS_PENALTY",
    )

    # Delete the live scoreboard on shutdown — otherwise it hangs statically.
    # Both SIGTERM (kill) and SIGINT (Ctrl+C) set a flag; loop checks it after
    # each sleep and breaks cleanly, then deletes the message.
    _terminate = [False]

    def _on_term(sig, frame):
        _terminate[0] = True

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    while True:
        if _terminate[0]:
            break
        event = fetch_scoreboard(event_id)
        if event is None:
            time.sleep(POLL_INTERVAL)
            continue

        comp = event["competitions"][0]
        status = comp["status"]
        current_state = status.get("type", {}).get("name", "")
        if current_state not in DELAY_STATUSES:
            # Defensive clear — don't rely solely on an "end-delay" commentary
            # item showing up; if the match has clearly resumed, drop any
            # stale reason rather than risk it lingering on the board.
            delay_reason = ""
        # 2026-06-17: back to ESPN's own display string (e.g. "53'")
        # instead of the mm:ss conversion — simpler, and matches what every
        # other soccer score feed shows.
        clock = status.get("type", {}).get("detail", "")
        if current_state == "STATUS_SCHEDULED":
            clock = _fmt_schedule_detail(clock)

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

        # If the very first poll sees a final state (e.g. watcher launched after
        # the match already ended), exit immediately — nothing to watch and we
        # don't want to keep editing a stale scoreboard forever.
        if last_state == "" and current_state in FINAL_STATES:
            print(f"[early-exit] match {event_id} already finished ({current_state}) — nothing to watch")
            break

        # Key event details (goals/cards — always post these)
        details = comp.get("details", [])
        goals_list = [
            {
                "minute": d.get("clock", {}).get("displayValue", "?"),
                "player": _scorer_name(d),
                "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
                "type": ("OWN GOAL" if d.get("ownGoal") else "pen." if d.get("penaltyKick") else "goal"),
            }
            for d in details if d.get("scoringPlay")
        ]
        cards_list = [
            {
                "minute": d.get("clock", {}).get("displayValue", "?"),
                "player": _scorer_name(d),
                "team": team_id_map.get(d.get("team", {}).get("id", ""), "?"),
                "type": ("red" if d.get("redCard") else "yellow"),
            }
            for d in details if d.get("redCard") or d.get("yellowCard")
        ]

        # State transitions — use announced_states (a set) instead of just
        # last_state so we don't re-post if ESPN oscillates between two states
        # (e.g. STATUS_END_OF_REGULATION ↔ STATUS_EXTRA_TIME on consecutive polls).
        if current_state != last_state:
            # ET states collapse into one announcement bucket so any ET variant
            # only ever fires the "Extra time" message once per match.
            ET_STATES = frozenset({"STATUS_EXTRA_TIME", "STATUS_OVER_TIME", "STATUS_OVERTIME"})
            announce_key = "ET" if current_state in ET_STATES else current_state

            if announce_key not in announced_states:
                announced_states.add(announce_key)
                if current_state == "STATUS_HALFTIME":
                    _post(f"⏸️ **HALF TIME** | {scoreline(scores, home_name, away_name)}")
                    scoreboard_buried_by += 1
                elif current_state == "STATUS_END_OF_REGULATION":
                    _post(f"⏱️ **END OF REGULATION** | {scoreline(scores, home_name, away_name)} — going to extra time")
                    scoreboard_buried_by += 1
                elif current_state in ET_STATES:
                    _post(f"**Extra time** | {scoreline(scores, home_name, away_name)}")
                    scoreboard_buried_by += 1
                elif current_state == "STATUS_HALFTIME_ET":
                    _post(f"⏸️ **ET HALF TIME** | {scoreline(scores, home_name, away_name)}")
                    scoreboard_buried_by += 1
            if current_state in ("STATUS_SHOOTOUT", "STATUS_PENALTY", "STATUS_PENALTY_KICKS") and "SHOOTOUT" not in announced_states:
                announced_states.add("SHOOTOUT")
                _post(f"🎯 **PENALTY SHOOTOUT** | {scoreline(scores, home_name, away_name)}")
                scoreboard_buried_by += 1
            elif current_state in FINAL_STATES and "FINAL" not in announced_states:
                if current_state == "STATUS_FINAL_PEN":
                    # Build final pen scoreline: n(n) format
                    home_pen = sum(1 for b in pen_shots for s in b.get("shots", [])
                                   if b["team"] == home_name and s.get("didScore"))
                    away_pen = sum(1 for b in pen_shots for s in b.get("shots", [])
                                   if b["team"] == away_name and s.get("didScore"))
                    h = scores.get(home_name, 0)
                    a = scores.get(away_name, 0)
                    ft_line = f"{home_name} {h}({home_pen}) – {a}({away_pen}) {away_name}"
                    _post(f"🏁 **FULL TIME (AET + pens)** | {ft_line}")
                else:
                    _post(f"🏁 **FULL TIME** | {scoreline(scores, home_name, away_name)}")
                if scoreboard_msg_id:
                    final_board = render_scoreboard(
                        home_name, away_name, scores, clock, current_state,
                        goals_list, cards_list, {}, injuries=injuries,
                        pen_shots=pen_shots or None,
                    )
                    edit_discord(channel_id, scoreboard_msg_id, final_board)
                announced_states.add("FINAL")
                archive_notebook(event_id)
                break
            last_state = current_state

        # Frozen-state watchdog: ESPN sometimes never transitions out of
        # STATUS_SECOND_HALF after the game ends. If status + score are frozen
        # for FROZEN_STATE_TIMEOUT while we're past the point a game should end,
        # treat it as FT and shut down cleanly.
        # Guard: STATUS_IN_PROGRESS at clock < 50' is likely halftime — don't
        # fire the watchdog then (halftime legitimately freezes score for ~15 min).
        _clock_mins = int(clock.split(":")[0]) if clock and ":" in clock else (int(clock.rstrip("'")) if clock and clock.rstrip("'").isdigit() else 99)
        _is_halftime_window = (current_state == "STATUS_IN_PROGRESS" and _clock_mins < 50)
        if current_state in POST_90_STATES and not _is_halftime_window:
            # Include the display clock in the key so stoppage-time ticks (90'+1', 90'+2', …)
            # reset the frozen timer — ESPN holds STATUS_SECOND_HALF through all of stoppage
            # time, so state+score alone would look frozen while the clock is still running.
            state_key = (current_state, scores.get(home_name, 0), scores.get(away_name, 0), clock)
            now_mono = time.monotonic()
            if state_key != _frozen_state_key:
                _frozen_state_key = state_key
                _frozen_state_since = now_mono
            elif now_mono - _frozen_state_since > FROZEN_STATE_TIMEOUT:
                print(f"[watchdog] state+score frozen for {FROZEN_STATE_TIMEOUT//60}+ min in {current_state} — forcing FT shutdown")
                _post(f"🏁 **FULL TIME** (watchdog) | {scoreline(scores, home_name, away_name)}")
                if scoreboard_msg_id:
                    final_board = render_scoreboard(
                        home_name, away_name, scores, clock, "STATUS_FULL_TIME",
                        goals_list, cards_list, {}, injuries=injuries,
                        pen_shots=pen_shots or None,
                    )
                    edit_discord(channel_id, scoreboard_msg_id, final_board)
                archive_notebook(event_id)
                break
        # uid keyed on (event kind, scorer id, team, occurrence index) rather
        # than just (kind, scorer, team) — a brace/hat-trick (or two cautions
        # on the same player) collapses to one uid under the simpler key, so
        # the 2nd+ event by the same player silently never fires (caught on
        # Mbappé's brace vs Iraq: 2nd goal showed in the scoreboard's GOALS
        # list but never got its own announcement). The occurrence index is
        # this detail's position among same-key entries within `details`
        # itself, which is stable across polls — also avoids keying on the
        # clock string, which can drift between polls for the same goal
        # (e.g. "40'" -> "45'+2'" stoppage-time correction) and caused the
        # same goal to repost twice before this uid scheme existed.
        occurrence_seen_this_poll: dict = {}
        for detail in details:
            player = _scorer_name(detail)
            kind = "goal" if detail.get("scoringPlay") else "red" if detail.get("redCard") else "yellow" if detail.get("yellowCard") else "other"
            key = (kind, _participant_athlete_id(detail), detail.get("team", {}).get("id", ""))
            occurrence = occurrence_seen_this_poll.get(key, 0)
            occurrence_seen_this_poll[key] = occurrence + 1
            uid = key + (occurrence,)
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
                _post(f"⚽ **GOAL{own}{pk}!** {d_clock} — {player} {emoji}\n> {scoreline(goals_so_far, home_name, away_name)}")
                scoreboard_buried_by += 1
            elif detail.get("redCard"):
                _post(f"🟥 **RED CARD** {d_clock} — {player} ({team_name})")
                scoreboard_buried_by += 1
            elif detail.get("yellowCard"):
                _post(f"🟨 Yellow card {d_clock} — {player} ({team_name})")
                scoreboard_buried_by += 1

        # Commentary + stats
        summary = fetch_summary(event_id)
        commentary = summary.get("commentary", [])

        # Match intro (fixture info + visual lineups) posts once at kickoff,
        # as soon as ESPN exposes the rosters (usually a few minutes
        # before/at kickoff, not pregame).
        if not lineups_posted:
            intro_text = format_match_intro(comp, summary, home_name, away_name)
            if intro_text:
                post_discord(channel_id, intro_text)
                lineups_posted = True
                open(intro_marker, "w").close()
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

        # Penalty shootout data — populated from summary once we're in a pen state
        if current_state in PENALTY_STATES or pen_shots:
            raw_shootout = summary.get("shootout", [])
            if raw_shootout:
                pen_shots = raw_shootout

        # Announce individual penalty kicks as they land (goal or save)
        if pen_shots:
            home_e_p, away_e_p = team_emoji(home_name), team_emoji(away_name)
            for block in pen_shots:
                team_key = block["team"]
                t_emoji = home_e_p if team_key == home_name else away_e_p
                t_disp = team_name(team_key, current_scoreboard_lang(watcher_start))
                for sh in block.get("shots", []):
                    key = (team_key, sh["shotNumber"])
                    if key in _pen_announced:
                        continue
                    _pen_announced.add(key)
                    p = sh.get("player", "?")
                    n = sh["shotNumber"]
                    if sh.get("didScore"):
                        _post(f"🟢 **Pen #{n}** {t_emoji} {t_disp} — {p} **SCORES**")
                    else:
                        _post(f"🔴 **Pen #{n}** {t_emoji} {t_disp} — {p} **SAVED/MISSED**")
                    scoreboard_buried_by += 1

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
            play_type = item.get("play", {}).get("type", {}).get("type", "")
            if play_type == "start-delay":
                delay_reason = text
            elif play_type == "end-delay":
                delay_reason = ""
            if is_key_moment(text):
                # Goals/cards already get permanent posts from scoreboard
                # details above; log everything to the notebook either way.
                commentary_log.append({"minute": minute, "text": text})
                lower = text.lower()
                if "var" in lower:
                    var_review_until = time.monotonic() + VAR_REVIEW_TIMEOUT
                if "goal" in lower or "red card" in lower or "yellow card" in lower:
                    continue  # already posted permanently above
                injury_match = _INJURY_SUB_RE.match(text)
                if injury_match:
                    # seen_commentary alone isn't reliable dedup here — ESPN's
                    # feed sometimes re-emits the same substitution under a new
                    # sequence number a poll or two later (the minute can even
                    # tick over by one), which seen_commentary's (seq, text[:40])
                    # key doesn't catch. Goals/cards avoid this because they're
                    # recomputed fresh from structured details every poll;
                    # injuries are accumulated by hand, so dedupe explicitly on
                    # (player, team) — the same player can't get hurt out twice.
                    player = injury_match.group("outgoing").strip()
                    team = injury_match.group("team").strip()
                    if not any(inj["player"] == player and inj["team"] == team for inj in injuries):
                        injuries.append({
                            "minute": minute,
                            "player": player,
                            "team": team,
                            "replaced_by": injury_match.group("incoming").strip(),
                        })
                    continue  # permanent entry, not ephemeral
                # 2026-06-17: full fidelity for everything else (subs,
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
            lang=current_scoreboard_lang(watcher_start),
            injuries=injuries,
            delay_reason=delay_reason,
            pen_shots=pen_shots or None,
        )
        if scoreboard_msg_id is None:
            scoreboard_msg_id = _post(board_text)
            polls_since_repost = 0
        elif scoreboard_buried_by > 0 or polls_since_repost >= REPOST_EVERY_POLLS:
            # Pinning used to handle "find it again," but each pin fires a
            # "X pinned a message" system notice every repost — 2026-06-17:
            # too noisy. Delete+repost alone already resurfaces it at the
            # bottom of the channel, which is the part that actually matters.
            # 2026-06-18: post the replacement *before* deleting the old one —
            # post_discord fails silently (network blip, rate limit) and used
            # to leave the scoreboard deleted with nothing replacing it.
            old_msg_id = scoreboard_msg_id
            new_msg_id = _post(board_text)
            if new_msg_id:
                delete_discord(channel_id, old_msg_id)
                if old_msg_id in posted_ids:
                    posted_ids.remove(old_msg_id)
                scoreboard_msg_id = new_msg_id
                scoreboard_buried_by = 0
                polls_since_repost = 0
            # else: keep editing the old message next poll instead of losing it
        else:
            edit_discord(channel_id, scoreboard_msg_id, board_text)

        # Update notebook every poll
        notebook = build_notebook(
            event_id, home_name, away_name, scores, clock,
            current_state, details, commentary_log, stats, team_id_map,
            injuries=injuries, delay_reason=delay_reason,
            posted_message_ids=posted_ids,
        )
        write_notebook(event_id, notebook)

        time.sleep(POLL_INTERVAL)
        if _terminate[0]:
            break

    if _terminate[0] and scoreboard_msg_id:
        delete_discord(channel_id, scoreboard_msg_id)
        print(f"[shutdown] deleted live scoreboard {scoreboard_msg_id}")

if __name__ == "__main__":
    main()
