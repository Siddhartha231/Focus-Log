"""Drives the terminal UI with Textual's test pilot and a fake clock."""

import asyncio
from datetime import datetime

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Digits, Input, Static  # noqa: E402

from focuslog import db, goals  # noqa: E402
from focuslog.tui import FocusApp, clock_text  # noqa: E402


def run(coroutine):
    # plain asyncio.run keeps the test suite free of async plugins
    asyncio.run(coroutine)


def sessions(conn):
    rows = conn.execute("SELECT subject, duration_seconds, status FROM sessions ORDER BY id")
    return [tuple(r) for r in rows]


def text_of(app, selector):
    return str(app.query_one(selector, Static).render())


def test_clock_text():
    assert clock_text(1500) == "25:00"
    assert clock_text(59) == "00:59"
    assert clock_text(90 * 60) == "90:00"


def test_type_a_subject_and_finish_a_round(conn, clock):
    alerts = []

    async def scenario():
        app = FocusApp(conn, minutes=1, break_minutes=1, rounds=1,
                       alert=lambda *a: alerts.append(a), clock=clock)
        async with app.run_test() as pilot:
            assert app.query_one("#subject", Input).has_focus
            await pilot.press(*"Math", "enter")
            assert app.pomo is not None
            assert text_of(app, "#phase") == "Study 1/1: Math"
            assert app.query_one("#subject", Input).display is False

            clock.advance(15)
            app.tick()
            assert app.query_one("#clock", Digits).value == "00:45"

            clock.advance(45)
            app.tick()
            assert app.pomo is None                       # back to the idle screen
            assert text_of(app, "#phase") == "All rounds done!"
            assert app.query_one("#week", DataTable).row_count == 1

    run(scenario())
    assert sessions(conn) == [("Math", 60, "completed")]
    assert alerts == [("FocusLog", "Round 1 done: Math")]


def test_space_pauses_and_resumes(conn, clock):
    async def scenario():
        app = FocusApp(conn, subject="OS", minutes=1, rounds=1, clock=clock)
        async with app.run_test() as pilot:
            clock.advance(10)
            await pilot.press("space")
            assert app.pomo.paused
            assert app.query_one("#clock", Digits).has_class("paused")

            clock.advance(600)                            # paused time must not count
            app.tick()
            assert app.query_one("#clock", Digits).value == "00:50"

            await pilot.press("space")
            assert not app.pomo.paused
            clock.advance(5)
            app.tick()
            assert app.query_one("#clock", Digits).value == "00:45"

    run(scenario())
    assert sessions(conn) == []


def test_s_stops_and_saves_partial_time(conn, clock):
    async def scenario():
        app = FocusApp(conn, subject="OS", minutes=25, clock=clock)
        async with app.run_test() as pilot:
            clock.advance(90)
            await pilot.press("s")
            assert app.pomo is None
            assert app.query_one("#subject", Input).has_focus   # ready for the next one

    run(scenario())
    assert sessions(conn) == [("OS", 90, "interrupted")]


def test_quitting_mid_round_saves_the_time(conn, clock):
    async def scenario():
        app = FocusApp(conn, subject="OS", minutes=25, clock=clock)
        async with app.run_test() as pilot:
            clock.advance(30)
            await pilot.press("q")

    run(scenario())
    assert sessions(conn) == [("OS", 30, "interrupted")]


def test_break_then_second_round(conn, clock):
    async def scenario():
        app = FocusApp(conn, subject="Math", minutes=1, break_minutes=1, rounds=2, clock=clock)
        async with app.run_test():
            clock.advance(60)
            app.tick()
            assert text_of(app, "#phase") == "Break 1/2"
            assert app.query_one("#clock", Digits).has_class("break")
            clock.advance(60)
            app.tick()
            assert text_of(app, "#phase") == "Study 2/2: Math"

    run(scenario())
    assert sessions(conn) == [("Math", 60, "completed")]


def test_side_panel_shows_week_and_goals(conn, clock):
    db.add_session(conn, "OS", datetime.now(), 3600, "completed")
    goals.set_goal(conn, "OS", 2)

    async def scenario():
        app = FocusApp(conn, clock=clock)
        async with app.run_test():
            assert app.query_one("#week", DataTable).row_count == 1
            assert text_of(app, "#today") == "Today: 01:00:00"
            assert "01:00:00 / 02:00:00" in text_of(app, "#goals")
            assert "[######------]" in text_of(app, "#goals")

    run(scenario())


def test_typing_s_and_q_in_the_subject_box_does_not_trigger_shortcuts(conn, clock):
    async def scenario():
        app = FocusApp(conn, minutes=1, rounds=1, clock=clock)
        async with app.run_test() as pilot:
            await pilot.press(*"sq n", "enter")
            assert app.pomo is not None and app.pomo.subject == "sq n"

    run(scenario())


def test_bad_settings_rejected(conn):
    with pytest.raises(ValueError):
        FocusApp(conn, minutes=0)
