# wc-watcher

A live World Cup match tracker that posts a continuously-updating scoreboard to a Discord channel by polling ESPN's public API.

## What it does

- Polls ESPN's scoreboard/summary endpoints every few seconds for a given match
- Posts a one-time **match intro** as soon as ESPN exposes rosters (venue, city, kickoff time in ET/PT, round, broadcast, referee, plus a formation-grouped visual lineup with jersey numbers for both teams)
- Posts a single Discord message and **edits it in place** as the match progresses (score, clock, stats, recent commentary) instead of spamming new messages
- Posts permanent announcements for goals and cards, deduped so each event only fires once
- Surfaces full-fidelity commentary (subs, injuries, dangerous chances, saves, VAR reviews) inside the scoreboard's "Live" section, aged out after a short window so it doesn't clutter
- Writes a live JSON "notebook" to `/tmp/wc_notebook_<event_id>.json` on every poll, so other tools/queries can read current match state without hitting ESPN again
- Archives the notebook to `completed/` on full time instead of deleting it, so finished-match notes stay queryable for the rest of the tournament

## What it looks like

Once per match, as soon as rosters are available, a fixture intro posts with venue/kickoff/broadcast info and a formation-grouped lineup:

```
════════════════════════════════
🇺🇸 United States vs Australia 🇦🇺
════════════════════════════════

FIFA World Cup, Group D
📍 Lumen Field, Seattle, Washington
🕐 3:00PM ET / 12:00PM PT
📺 FOX, Tele, FOX One
🟨 Referee: Felix Zwayer

🇺🇸 United States (3-5-2)
────────────────────────────────
  GK  #24 M. Freese
  DEF  #13 T. Ream · #3 C. Richards · #16 A. Freeman
  MID  #2 S. Dest · #5 A. Robinson · #8 W. McKennie · #17 M. Tillman · #4 T. Adams
  FWD  #9 R. Pepi · #20 F. Balogun

🇦🇺 Australia (5-4-1)
────────────────────────────────
  GK  #18 P. Beach
  DEF  #4 J. Italiano · #5 J. Bos · #21 C. Burgess · #19 H. Souttar · #3 A. Circati
  MID  #7 M. Leckie · #13 A. O'Neill · #24 P. Okon-Engstler · #23 N. Velupillay
  FWD  #9 M. Toure
```
```
════════════════════════════════
     🇺🇸 美国 vs 澳大利亚 🇦🇺     
════════════════════════════════

FIFA World Cup, Group D
📍 Lumen Field, Seattle, Washington
🕐 3:00PM ET / 12:00PM PT
📺 FOX, Tele, FOX One
🟨 裁判: Felix Zwayer

🇺🇸 美国 (3-5-2)
────────────────────────────────
  门将  #24 M. Freese
  后卫  #13 T. Ream · #3 C. Richards · #16 A. Freeman
  中场  #2 S. Dest · #5 A. Robinson · #8 W. McKennie · #17 M. Tillman · #4 T. Adams
  前锋  #9 R. Pepi · #20 F. Balogun

🇦🇺 澳大利亚 (5-4-1)
────────────────────────────────
  门将  #18 P. Beach
  后卫  #4 J. Italiano · #5 J. Bos · #21 C. Burgess · #19 H. Souttar · #3 A. Circati
  中场  #7 M. Leckie · #13 A. O'Neill · #24 P. Okon-Engstler · #23 N. Velupillay
  前锋  #9 M. Toure
```

From kickoff onward, the scoreboard is a single Discord message, edited in place each poll. It posts an English block followed by a Chinese block:

```
════════════════════════════════
     🇺🇸 United States vs Australia 🇦🇺      
             2 - 0              
        · 1st half 34' ·        
════════════════════════════════

GOALS
────────────────────────────────
⚽ 16' Ricardo Pepi (United States)
⚽ 29' Folarin Balogun (United States)

CARDS
────────────────────────────────
🟨 9' Tim Ream (United States)
🟥 33' Harry Souttar (Australia)

SHOTS (ON TARGET)
────────────────────────────────
United States: 7 (4)
Australia: 2 (0)

POSSESSION  66.2% – 33.8%
[████████████████████░░░░░░░░░░]

MATCH STATS
────────────────────────────────
Pass acc.  88% – 65%
Corners  3 – 1
Fouls  2 – 4

LIVE
────────────────────────────────
33' Harry Souttar (Australia) is shown the red card.
33' VAR Decision: No Penalty United States.
```
```
════════════════════════════════
    🇺🇸 美国 vs 澳大利亚 🇦🇺      
             2 - 0              
         · 上半场 34' ·         
════════════════════════════════

进球
────────────────────────────────
⚽ 16' Ricardo Pepi (美国)
⚽ 29' Folarin Balogun (美国)

红黄牌
────────────────────────────────
🟨 9' Tim Ream (美国)
🟥 33' Harry Souttar (澳大利亚)

射门（射正）
────────────────────────────────
美国: 7 (4)
澳大利亚: 2 (0)

控球  66.2% – 33.8%
[████████████████████░░░░░░░░░░]

比赛数据
────────────────────────────────
传球成功率  88% – 65%
角球  3 – 1
犯规  2 – 4

实时
────────────────────────────────
33' Harry Souttar (Australia) is shown the red card.
33' VAR Decision: No Penalty United States.
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
