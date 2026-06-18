# wc-watcher

A live World Cup match tracker that posts a continuously-updating scoreboard to a Discord channel by polling ESPN's public API.

## What it does

- Polls ESPN's scoreboard/summary endpoints every few seconds for a given match
- Posts a single Discord message and **edits it in place** as the match progresses (score, clock, stats, recent commentary) instead of spamming new messages
- Posts permanent announcements for goals and cards, deduped so each event only fires once
- Surfaces full-fidelity commentary (subs, injuries, dangerous chances, saves, VAR reviews) inside the scoreboard's "Live" section, aged out after a short window so it doesn't clutter
- Writes a live JSON "notebook" to `/tmp/wc_notebook_<event_id>.json` on every poll, so other tools/queries can read current match state without hitting ESPN again
- Archives the notebook to `completed/` on full time instead of deleting it, so finished-match notes stay queryable for the rest of the tournament

## What it looks like

The scoreboard is a single Discord message, edited in place each poll. It posts an English block followed by a Chinese block:

```
════════════════════════════════
     🇨🇦 Canada vs Qatar 🇶🇦      
             2 - 0              
        · 1st half 34' ·        
════════════════════════════════

GOALS
────────────────────────────────
⚽ 16' Cyle Larin (Canada)
⚽ 29' Jonathan David (Canada)

CARDS
────────────────────────────────
🟨 9' Derek Cornelius (Canada)
🟥 33' Homam El Amin (Qatar)

SHOTS (ON TARGET)
────────────────────────────────
Canada: 7 (4)
Qatar: 2 (0)

POSSESSION  66.2% – 33.8%
[████████████████████░░░░░░░░░░]

MATCH STATS
────────────────────────────────
Pass acc.  88% – 65%
Corners  3 – 1
Fouls  2 – 4

LIVE
────────────────────────────────
33' Homam El Amin (Qatar) is shown the red card.
33' VAR Decision: No Penalty Canada.
```
```
════════════════════════════════
     🇨🇦 加拿大 vs 卡塔尔 🇶🇦     
             2 - 0              
         · 上半场 34' ·         
════════════════════════════════

进球
────────────────────────────────
⚽ 16' Cyle Larin (加拿大)
⚽ 29' Jonathan David (加拿大)

红黄牌
────────────────────────────────
🟨 9' Derek Cornelius (加拿大)
🟥 33' Homam El Amin (卡塔尔)

射门（射正）
────────────────────────────────
加拿大: 7 (4)
卡塔尔: 2 (0)

控球  66.2% – 33.8%
[████████████████████░░░░░░░░░░]

比赛数据
────────────────────────────────
传球成功率  88% – 65%
角球  3 – 1
犯规  2 – 4

实时
────────────────────────────────
33' Homam El Amin (Qatar) is shown the red card.
33' VAR Decision: No Penalty Canada.
```

GOALS/CARDS/MATCH STATS/LIVE sections only appear once there's something to show in them — a 0' kickoff scoreboard is just the header block.

## Why it's built this way

ESPN exposes the same "who scored / who got carded" concept with two different JSON shapes depending on the endpoint (`scoreboard` uses `athletesInvolved`, `summary` uses `participants`) — the code normalizes both. Pass/shot accuracy fields ESPN reports are pre-rounded to one decimal, so percentages are computed from the raw counts instead, since the rounded version can look frozen for long stretches of a match even as the underlying numbers move.

## Setup

```bash
pip install -r requirements.txt
```

Needs a Discord bot token with permission to post/edit/delete messages in the target channel. Provide it one of:

- `DISCORD_BOT_TOKEN` environment variable, or
- a `.env` file next to `launch_watcher.sh` containing `DISCORD_BOT_TOKEN=...` (gitignored, never commit this), or
- `WC_ENV_FILE=/path/to/.env` pointing at one elsewhere

## Usage

Find an event ID from ESPN's scoreboard (or use `schedule.py` below), then:

```bash
bash launch_watcher.sh <EVENT_ID> <DISCORD_CHANNEL_ID>
```

Logs to `/tmp/wc_watcher_<EVENT_ID>.log`.

### Schedule lookup

```bash
python3 schedule.py [days_ahead]
```

Prints fixtures for today plus `days_ahead` additional days (default 2), with kickoff times in ET/PT and status.

## Notes

- `completed/` (archived notebooks) and `*.log` are gitignored — meant to be cleared out after the tournament ends, not kept forever.
- No state is persisted across a process restart beyond the notebook file — if the watcher restarts mid-match, in-memory dedup for commentary resets (cosmetic duplicate notebook entries are possible; permanent goal/card announcements are unaffected since those use a separate persistent check).
