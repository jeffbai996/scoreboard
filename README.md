# wc-watcher

A live World Cup match tracker that posts a continuously-updating scoreboard to a Discord channel by polling ESPN's public API.

## What it does

- Polls ESPN's scoreboard/summary endpoints every few seconds for a given match
- Posts a single Discord message and **edits it in place** as the match progresses (score, clock, stats, recent commentary) instead of spamming new messages
- Posts permanent announcements for goals and cards, deduped so each event only fires once
- Surfaces full-fidelity commentary (subs, injuries, dangerous chances, saves, VAR reviews) inside the scoreboard's "Live" section, aged out after a short window so it doesn't clutter
- Writes a live JSON "notebook" to `/tmp/wc_notebook_<event_id>.json` on every poll, so other tools/queries can read current match state without hitting ESPN again
- Archives the notebook to `completed/` on full time instead of deleting it, so finished-match notes stay queryable for the rest of the tournament

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
