import sqlite3
from datetime import datetime

from focuslog import db


def test_new_database_is_at_latest_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_database_from_the_original_script_upgrades_in_place(tmp_path):
    # build a database exactly the way the old single-file focuslog.py did
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY, subject TEXT, started_at TEXT,
                    duration_seconds INTEGER, status TEXT)""")
    old.execute("INSERT INTO sessions (subject, started_at, duration_seconds, status) "
                "VALUES ('Math', '2026-09-13T10:00:00', 1500, 'completed')")
    old.execute("INSERT INTO sessions (subject, started_at, duration_seconds, status) "
                "VALUES ('OS', '2026-09-13T11:00:00', 600, 'interrupted')")
    old.commit()
    old.close()

    conn = db.connect(path)
    rows = conn.execute("SELECT subject, uuid, dirty, deleted FROM sessions ORDER BY id").fetchall()
    assert [r["subject"] for r in rows] == ["Math", "OS"]
    # every old row got its own id and is queued for upload
    assert len({r["uuid"] for r in rows}) == 2
    assert all(r["dirty"] == 1 and r["deleted"] == 0 for r in rows)
    conn.close()


def test_opening_twice_does_not_rerun_migrations(tmp_path):
    path = tmp_path / "twice.db"
    db.connect(path).close()
    db.connect(path).close()  # would raise "duplicate column" if it re-ran


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    def broken(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + [broken])
    path = tmp_path / "broken.db"
    try:
        db.connect(path)
    except RuntimeError:
        pass
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) - 1
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "half_done" not in tables


def test_add_and_list_sessions(conn):
    db.add_session(conn, "Math", datetime(2026, 9, 13, 10, 0), 1500, "completed")
    db.add_session(conn, "OS", datetime(2026, 9, 14, 10, 0), 900, "interrupted")
    rows = db.recent_sessions(conn)
    assert [r["subject"] for r in rows] == ["OS", "Math"]  # newest first


def test_delete_is_a_soft_delete(conn):
    session_id = db.add_session(conn, "Math", datetime(2026, 9, 13, 10, 0), 1500, "completed")
    conn.execute("UPDATE sessions SET dirty = 0")
    db.delete_session(conn, session_id)
    assert db.recent_sessions(conn) == []
    row = conn.execute("SELECT deleted, dirty FROM sessions").fetchone()
    assert (row["deleted"], row["dirty"]) == (1, 1)  # tombstone kept, queued for sync


def test_find_by_prefix_escapes_wildcards(conn):
    db.add_session(conn, "Math", datetime(2026, 9, 13, 10, 0), 1500, "completed")
    assert db.find_by_prefix(conn, "%") == []
    assert db.find_by_prefix(conn, "_") == []


def test_summary_query_uses_the_index(conn):
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT subject, COUNT(*), SUM(duration_seconds) FROM sessions "
        "WHERE deleted = 0 AND started_at >= ? AND started_at < ? GROUP BY subject",
        ("2026-09-13", "2026-09-14"),
    ).fetchall()
    assert any("idx_sessions_started_at" in row[-1] for row in plan)
