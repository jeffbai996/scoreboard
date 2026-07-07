"""Status-line rendering tests for the scoreboard header.

The bug this guards against: break/end statuses (ET halftime, penalties, AET)
where ESPN emits descriptive English text in its `detail` field instead of a
clock. That raw English was being appended after the translated status label,
producing lines like "· ET half time Extra Time Halftime ·". A real clock
("53'") or a scheduled kickoff time must still show.
"""
from wc_watcher import _render_board_lines


def _status_line(clock: str, status: str, lang: int = 0) -> str:
    """Pull the single `· ... ·` status line out of the rendered board."""
    lines = _render_board_lines(
        "Switzerland", "Colombia", {"Switzerland": 0, "Colombia": 0},
        clock, status, goals=[], cards=[], stats={}, recent=None,
        var_review=False, lang=lang,
    )
    hits = [l.strip() for l in lines if l.strip().startswith("·")]
    assert hits, f"no status line rendered for {status!r}"
    return hits[0]


def test_et_halftime_does_not_leak_raw_english_detail():
    # ESPN sends detail="Extra Time Halftime" for STATUS_HALFTIME_ET.
    line = _status_line("Extra Time Halftime", "STATUS_HALFTIME_ET", lang=0)
    assert "Extra Time Halftime" not in line
    assert "ET half time" in line


def test_et_halftime_chinese_is_fully_translated():
    line = _status_line("Extra Time Halftime", "STATUS_HALFTIME_ET", lang=1)
    assert "Extra Time Halftime" not in line
    assert "加时半场" in line


def test_penalties_detail_not_appended():
    line = _status_line("Penalties", "STATUS_SHOOTOUT", lang=0)
    assert line.count("Penalties") == 1  # label only, detail suppressed


def test_live_clock_still_shows():
    line = _status_line("53'", "STATUS_SECOND_HALF", lang=0)
    assert "53'" in line


def test_stoppage_time_clock_still_shows():
    line = _status_line("90'+3'", "STATUS_SECOND_HALF", lang=0)
    assert "90'+3'" in line


def test_scheduled_kickoff_time_still_shows():
    line = _status_line("Thu, Jun. 25 @ 7:00 PM ET", "STATUS_SCHEDULED", lang=0)
    assert "7:00 PM ET" in line


# ESPN spells this status "STATUS_END_OF_EXTRATIME" (EXTRATIME, one word) — the
# map had STATUS_END_OF_EXTRA_TIME (two words), so it fell through to an
# untranslated English fallback ("End Of Extratime") on BOTH renders. Confirmed
# live off event 760508 (detail="AET-pens", state="in", i.e. ET done → pens).
def test_end_of_extratime_one_word_english():
    line = _status_line("AET-pens", "STATUS_END_OF_EXTRATIME", lang=0)
    assert "End Of Extratime" not in line   # no raw fallback
    assert "AET-pens" not in line           # no raw detail
    assert "ET" in line and "Penalties" in line


def test_end_of_extratime_one_word_chinese():
    line = _status_line("AET-pens", "STATUS_END_OF_EXTRATIME", lang=1)
    # Chinese render must be fully translated — no Latin status text at all.
    assert "Extratime" not in line
    assert "AET" not in line
    assert "点球" in line


def test_status_lookup_is_underscore_insensitive():
    # The same words spelled with different underscores must resolve to the
    # same label — kills the "EXTRATIME vs EXTRA_TIME" class, not just this one.
    a = _status_line("", "STATUS_END_OF_EXTRA_TIME", lang=1)
    b = _status_line("AET-pens", "STATUS_END_OF_EXTRATIME", lang=1)
    # both are ET-end states; neither should show raw English
    assert "Extratime" not in a and "Extratime" not in b
