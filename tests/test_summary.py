from datetime import date, datetime

from focuslog import db, goals
from focuslog.summary import format_time, period_bounds, summary_lines


def add(conn, subject, when, seconds):
    return db.add_session(conn, subject, datetime.fromisoformat(when), seconds, "completed")


def test_format_time():
    assert format_time(0) == "00:00:00"
    assert format_time(3725) == "01:02:05"


def test_week_starts_on_monday():
    # 2026-09-13 is a Sunday
    assert period_bounds("weekly", date(2026, 9, 13)) == (date(2026, 9, 7), date(2026, 9, 13))
    assert period_bounds("weekly", date(2026, 9, 14)) == (date(2026, 9, 14), date(2026, 9, 20))


def test_daily_summary_matches_original_format(conn):
    add(conn, "Math", "2026-09-13T09:00:00", 1500)
    add(conn, "Math", "2026-09-13T23:59:59", 1500)   # last second of the day still counts
    add(conn, "OS", "2026-09-13T12:00:00", 3000)
    add(conn, "OS", "2026-09-14T00:00:00", 999)      # next day, must not count
    assert summary_lines(conn, "daily", date(2026, 9, 13)) == [
        "Daily summary for 2026-09-13",
        "Subject\t\tSessions\tTime",
        "Math\t\t2\t\t00:50:00",
        "OS\t\t1\t\t00:50:00",
        "Total: 3 sessions, 01:40:00",
    ]


def test_weekly_summary_lists_every_day(conn):
    add(conn, "Math", "2026-09-07T09:00:00", 1500)   # Monday
    add(conn, "Math", "2026-09-13T09:00:00", 600)    # Sunday
    add(conn, "Math", "2026-09-06T09:00:00", 9999)   # Sunday before, outside the week
    lines = summary_lines(conn, "weekly", date(2026, 9, 10))
    assert lines[0] == "Weekly summary: 2026-09-07 through 2026-09-13"
    assert "Total: 2 sessions, 00:35:00" in lines
    assert lines[-7] == "2026-09-07 (Mon): 00:25:00"
    assert lines[-5] == "2026-09-09 (Wed): 00:00:00"
    assert lines[-1] == "2026-09-13 (Sun): 00:10:00"


def test_empty_period(conn):
    assert summary_lines(conn, "daily", date(2026, 9, 13)) == [
        "Daily summary for 2026-09-13",
        "No study sessions recorded for this period.",
    ]


def test_deleted_sessions_are_left_out(conn):
    session_id = add(conn, "Math", "2026-09-13T09:00:00", 1500)
    db.delete_session(conn, session_id)
    assert summary_lines(conn, "daily", date(2026, 9, 13))[1].startswith("No study sessions")


def test_goal_progress(conn):
    goals.set_goal(conn, "OS", 2)                     # 2 hours a week
    goals.set_goal(conn, "Math", 1)
    add(conn, "OS", "2026-09-08T09:00:00", 3600)
    add(conn, "OS", "2026-08-01T09:00:00", 3600)      # a different week
    progress = {g.subject: g for g in goals.weekly_progress(conn, date(2026, 9, 10))}
    assert progress["OS"].done_seconds == 3600
    assert progress["OS"].fraction == 0.5
    assert progress["Math"].done_seconds == 0
    assert goals.progress_bar(0.5, 10) == "[#####-----]"


def test_goal_update_and_remove(conn):
    goals.set_goal(conn, "OS", 2)
    goals.set_goal(conn, "OS", 5)                     # setting again replaces it
    assert goals.weekly_progress(conn, date(2026, 9, 10))[0].goal_seconds == 5 * 3600
    assert goals.remove_goal(conn, "OS") is True
    assert goals.remove_goal(conn, "OS") is False
