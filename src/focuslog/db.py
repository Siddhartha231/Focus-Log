"""Local SQLite storage: connection, schema migrations and session queries."""

import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path


def home_dir() -> Path:
    # FOCUSLOG_HOME lets tests (and curious users) point everything somewhere else
    return Path(os.environ.get("FOCUSLOG_HOME", "~/.focuslog")).expanduser()


def default_db_path() -> Path:
    return home_dir() / "focuslog.db"


def now_ms() -> int:
    """Current time as milliseconds since the epoch (UTC). Used for updated_at."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Migrations
#
# Each function upgrades the schema by one version. The current version lives
# in SQLite's built-in "PRAGMA user_version", so a database made by any older
# FocusLog (including the original single-file script) upgrades in place.
# ---------------------------------------------------------------------------

def _migrate_1(conn: sqlite3.Connection) -> None:
    # the original table from the single-file version
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               id INTEGER PRIMARY KEY,
               subject TEXT,
               started_at TEXT,
               duration_seconds INTEGER,
               status TEXT
           )"""
    )


def _migrate_2(conn: sqlite3.Connection) -> None:
    # columns needed for syncing between devices
    conn.execute("ALTER TABLE sessions ADD COLUMN uuid TEXT")
    conn.execute("ALTER TABLE sessions ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE sessions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE sessions ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1")

    # give every existing row a global id so it can be uploaded later
    stamp = now_ms()
    rows = conn.execute("SELECT id FROM sessions WHERE uuid IS NULL").fetchall()
    for (row_id,) in rows:
        conn.execute(
            "UPDATE sessions SET uuid = ?, updated_at = ? WHERE id = ?",
            (str(uuid.uuid4()), stamp, row_id),
        )

    conn.execute("CREATE UNIQUE INDEX idx_sessions_uuid ON sessions (uuid)")
    # summaries filter on started_at, so index it
    conn.execute("CREATE INDEX idx_sessions_started_at ON sessions (started_at)")
    conn.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT)")


def _migrate_3(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE goals (
               subject TEXT PRIMARY KEY,
               weekly_seconds INTEGER NOT NULL
           )"""
    )


MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3]


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, step in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        # one transaction per step, so a failed upgrade leaves the old schema intact
        conn.execute("BEGIN")
        try:
            step(conn)
            conn.execute("PRAGMA user_version = %d" % number)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        path = default_db_path()
    path = Path(path).expanduser()
    # make the folder if it doesn't exist yet
    path.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level=None means we control transactions ourselves
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def add_session(conn, subject: str, started_at: datetime, duration_seconds: int,
                status: str) -> str:
    """Save one study session and return its uuid."""
    session_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO sessions
               (uuid, subject, started_at, duration_seconds, status, updated_at, deleted, dirty)
           VALUES (?, ?, ?, ?, ?, ?, 0, 1)""",
        (session_id, subject, started_at.isoformat(), int(duration_seconds), status, now_ms()),
    )
    return session_id


def recent_sessions(conn, limit: int = 10) -> list:
    return conn.execute(
        """SELECT uuid, subject, started_at, duration_seconds, status
           FROM sessions
           WHERE deleted = 0
           ORDER BY started_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def find_by_prefix(conn, prefix: str) -> list:
    """Sessions whose id starts with prefix (like short git hashes)."""
    # escape LIKE wildcards so "%" can't match everything
    safe = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT uuid, subject, started_at, duration_seconds FROM sessions "
        "WHERE deleted = 0 AND uuid LIKE ? ESCAPE '\\'",
        (safe + "%",),
    ).fetchall()


def delete_session(conn, session_id: str) -> None:
    # Soft delete. The row stays as a "tombstone" so the delete can sync to
    # other devices; summaries just skip it.
    conn.execute(
        "UPDATE sessions SET deleted = 1, dirty = 1, updated_at = ? WHERE uuid = ?",
        (now_ms(), session_id),
    )


# ---------------------------------------------------------------------------
# Small key/value store used by sync (cursor, which account this db belongs to)
# ---------------------------------------------------------------------------

def get_state(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return default if row is None else row[0]


def set_state(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
