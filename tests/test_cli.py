import pytest

from focuslog import cli, db
from focuslog.pomodoro import Pomodoro


def run(tmp_path, *argv):
    return cli.main(["--db", str(tmp_path / "cli.db"), *argv])


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "Pomodoro study tracker" in capsys.readouterr().out


def test_timer_runs_all_rounds_and_saves(conn, clock):
    printed = []
    alerts = []
    pomo = Pomodoro("Math", 60, 10, 2)
    cli.run_timer(conn, pomo, out=lambda *a, **k: printed.append(a[0] if a else ""),
                  clock=clock, sleep=clock.sleep, alert=lambda *a: alerts.append(a))

    rows = conn.execute("SELECT subject, duration_seconds, status FROM sessions").fetchall()
    assert [tuple(r) for r in rows] == [("Math", 60, "completed")] * 2
    assert printed.count("Saved 00:01:00 for Math.") == 2
    assert printed[-1] == "All rounds done!"
    assert any("Study 1/2: Math | 00:00:59 left" in line for line in printed)
    assert len(alerts) == 3   # two rounds and one break


def test_ctrl_c_saves_partial_time(conn, clock):
    def sleep_then_interrupt(seconds):
        clock.advance(seconds)
        if clock.now >= 1000 + 7:
            raise KeyboardInterrupt

    printed = []
    pomo = Pomodoro("OS", 1500, 300, 4)
    cli.run_timer(conn, pomo, out=lambda *a, **k: printed.append(a[0] if a else ""),
                  clock=clock, sleep=sleep_then_interrupt)

    row = conn.execute("SELECT duration_seconds, status FROM sessions").fetchone()
    assert tuple(row) == (7, "interrupted")
    assert printed[-2:] == ["Saved 00:00:07 for OS.", "Stopped early."]


@pytest.mark.parametrize("argv, message", [
    (["start", "Math", "--minutes", "0"], "Error: minutes must be positive"),
    (["start", "Math", "--rounds", "0"], "Error: rounds must be positive"),
    (["start", "Math", "--break-minutes", "-1"], "Error: break minutes can't be negative"),
    (["start", "  "], "Error: subject can't be empty"),
    (["summary", "daily", "--date", "13/09/2026"], "Error: date must look like 2026-09-13"),
])
def test_bad_input(tmp_path, capsys, argv, message):
    assert run(tmp_path, *argv) == 2
    assert message in capsys.readouterr().out


def test_summary_log_delete_roundtrip(tmp_path, capsys):
    from datetime import datetime
    conn = db.connect(tmp_path / "cli.db")
    session_id = db.add_session(conn, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    conn.close()

    assert run(tmp_path, "summary", "daily", "--date", "2026-09-13") == 0
    assert "Math\t\t1\t\t00:25:00" in capsys.readouterr().out

    assert run(tmp_path, "log") == 0
    assert session_id[:8] in capsys.readouterr().out

    assert run(tmp_path, "delete", "zzzz") == 1
    capsys.readouterr()
    assert run(tmp_path, "delete", session_id[:8]) == 0
    assert "Deleted" in capsys.readouterr().out

    assert run(tmp_path, "log") == 0
    assert "No sessions yet." in capsys.readouterr().out


def test_goal_commands(tmp_path, capsys):
    assert run(tmp_path, "goal", "list") == 0
    assert "No goals yet" in capsys.readouterr().out
    assert run(tmp_path, "goal", "set", "OS", "10") == 0
    assert run(tmp_path, "goal", "set", "OS", "-1") == 2
    capsys.readouterr()
    assert run(tmp_path, "goal", "list") == 0
    assert "00:00:00 / 10:00:00 (0%)" in capsys.readouterr().out
    assert run(tmp_path, "goal", "remove", "OS") == 0
    assert run(tmp_path, "goal", "remove", "OS") == 1


def test_status_and_sync_when_logged_out(tmp_path, capsys):
    assert run(tmp_path, "status") == 0
    assert "Not logged in" in capsys.readouterr().out
    assert run(tmp_path, "sync") == 1
    assert "not logged in" in capsys.readouterr().out
    assert run(tmp_path, "logout") == 0
