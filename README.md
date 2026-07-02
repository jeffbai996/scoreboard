# wc-watcher

A live World Cup match tracker that posts a continuously-updating scoreboard to a Discord channel by polling ESPN's public API.

## What it does

- Polls ESPN's scoreboard/summary endpoints every few seconds for a given match
- Posts a one-time **match intro** (English) as soon as ESPN exposes rosters: venue, city, kickoff time in ET/PT, round, broadcast, referee, group standings, recent form (last 5 results, color-coded), last head-to-head meeting, and a formation-grouped visual lineup with jersey numbers and live standout-performer stats for both teams
- Posts a single Discord message and **edits it in place** as the match progresses (score, clock, stats, recent commentary) instead of spamming new messages — alternates between English and Chinese every 30s
- Posts permanent announcements for goals and cards, deduped so each event only fires once
- Surfaces full-fidelity commentary (subs, injuries, dangerous chances, saves, VAR reviews) inside the scoreboard's "Live" section, aged out after a short window so it doesn't clutter
- Writes a live JSON "notebook" to `/tmp/wc_notebook_<event_id>.json` on every poll, so other tools/queries can read current match state without hitting ESPN again
- Archives the notebook to `completed/` on full time instead of deleting it, so finished-match notes stay queryable for the rest of the tournament

## What it looks like

Once per match, as soon as rosters are available, a fixture intro posts with venue/kickoff/broadcast info, standings, recent form, head-to-head history, and a formation-grouped lineup (English only):

```
════════════════════════════════
🇺🇸 United States vs Australia 🇦🇺
════════════════════════════════

FIFA World Cup, Group D
📍 Lumen Field, Seattle, Washington
🕐 3:00PM ET / 12:00PM PT
📺 FOX, Tele, FOX One
🟨 Referee: Felix Zwayer

STANDINGS
────────────────────────────────
1. United States  2-0-0  GD  +5  Pts  6
2. Australia      1-0-1  GD   0  Pts  3
3. Türkiye         0-0-1  GD  -2  Pts  0
4. Paraguay        0-0-1  GD  -3  Pts  0

RECENT FORM
────────────────────────────────
🇺🇸 United States  🟩 🟩 🟨 🟩 🟥
🇦🇺 Australia      🟩 🟥 🟩 🟨 🟩

LAST MEETING
────────────────────────────────
2025-10-15  United States 2-1 Australia

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

STANDOUTS
────────────────────────────────
🇺🇸 United States
  Total Shots: Dest (3)
  Accurate Passes: Richards (91)
  Defensive Interventions: Richards (12)
  Saves: Freese (2)

🇦🇺 Australia
  Total Shots: Circati (1)
  Accurate Passes: Okon-Engstler (31)
  Defensive Interventions: Circati (13)
  Saves: Beach (1)
```

Recent form (last 5 results) uses color squares (🟩 win · 🟨 draw · 🟥 loss) instead of literal text or ANSI color codes — Discord only renders ANSI color on desktop, mobile shows raw escape characters as garbage, so squares are the only "colored" option that's actually cross-platform. The name column is padded to the longer of the two team names so the squares line up regardless of "Sweden" vs "Netherlands". STANDOUTS lists one stat per line per team rather than a single `·`-joined line, since the joined version wraps badly at phone width once a team has 4-5 categories.

From kickoff onward, the scoreboard is a single Discord message, edited in place each poll. It alternates between an English render and a Chinese render every 30 seconds (same data, language swaps on a fixed wall-clock interval independent of poll cadence) — English:

```
════════════════════════════════
     🇺🇸 USA vs Australia 🇦🇺     
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
🟥 [33'] Harry Souttar (Australia) is shown the red card.
🎯 [33'] VAR Decision: No Penalty United States.
```

...and 30 seconds later, the same message gets edited to:

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
🟥 [33'] Harry Souttar (Australia) is shown the red card.
🎯 [33'] VAR Decision: No Penalty United States.
```

GOALS/CARDS/MATCH STATS/LIVE sections only appear once there's something to show in them — a 0' kickoff scoreboard is just the header block. The full-time/final board always renders in English regardless of the alternation cycle.

## Penalty shootouts

When a match goes to penalties, the watcher:

- Posts a one-time **🎯 PENALTY SHOOTOUT** announcement (fires once on transition, never repeated)
- Posts a permanent announcement for **every individual kick** as it lands — a green circle for goals, red for saves/misses, including the round number and player name
- Adds a **PENALTIES / 点球大战** section to the scoreboard showing the ⬤/✕ dot grid per team, with individual player names and results below each row — works for any number of rounds (handles sudden death beyond the initial five)
- Shows the **score in `n(n)` format** while pens are live and on the final board — e.g. `1(4) – 1(3)` (regulation score + penalty tally)
- The final **FULL TIME (AET + pens)** announcement includes the same `n(n)` format

Example scoreboard during a shootout:

```
════════════════════════════════
  🇩🇪 Germany vs Paraguay 🇵🇾   
         1(3) – 1(4)           
       · Final (pens) ·        
════════════════════════════════

PENALTIES
────────────────────────────────
         3 – 4          
🇩🇪 Germany
   ✕ ⬤ ⬤ ✕ ⬤ ✕
   1. Havertz ✕
   2. Kimmich ⬤
   3. Musiala ⬤
   4. Woltemade ✕
   5. Amiri ⬤
   6. Tah ✕
🇵🇾 Paraguay
   ⬤ ⬤ ⬤ ✕ ✕ ⬤
   1. Maurício ⬤
   2. Gómez ⬤
   3. Galarza ⬤
   4. Sanabria ✕
   5. Balbuena ✕
   6. Canale ⬤
```

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

### Cron scheduling

```bash
python3 setup_crons.py <DISCORD_CHANNEL_ID> <EVENT_ID> [<EVENT_ID> ...]
```

Looks up each event's kickoff time from ESPN, converts UTC to the server's
local time, and writes a crontab entry that fires `launch_watcher.sh` a few
minutes before kickoff — so a batch of matches can be queued ahead of time
instead of launched by hand as each one starts.

## Notes

- `completed/` (archived notebooks) and `*.log` are gitignored — meant to be cleared out after the tournament ends, not kept forever.
- No state is persisted across a process restart beyond the notebook file — if the watcher restarts mid-match, in-memory dedup for commentary resets (cosmetic duplicate notebook entries are possible; permanent goal/card announcements are unaffected since those use a separate persistent check).
