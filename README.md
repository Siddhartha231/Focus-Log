# FocusLog

[![CI](https://github.com/Siddhartha231/Focus-Log/actions/workflows/ci.yml/badge.svg)](https://github.com/Siddhartha231/Focus-Log/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Siddhartha231/Focus-Log/branch/main/graph/badge.svg)](https://codecov.io/gh/Siddhartha231/Focus-Log)

A Pomodoro study tracker that lives in the terminal. It started as one Python
file that logged sessions to SQLite. It's now three parts:

- a **command-line timer** with daily and weekly summaries and weekly goals
- a **full-screen terminal UI** with pause/resume and notifications
- a **sync server** so sessions logged on one computer show up on the others

It's offline-first: the timer never needs a network. Sessions are saved locally
and uploaded whenever you next run `focuslog sync`.

![The terminal UI](docs/tui.svg)

## Install

Needs Python 3.10 or newer.

```bash
git clone https://github.com/Siddhartha231/Focus-Log
cd Focus-Log
pip install ".[tui]"        # or just "pip install ." for the plain CLI, which has no dependencies
```

## Usage

```bash
focuslog start "Data Structures"
focuslog start "OS" --minutes 50 --break-minutes 10 --rounds 2
focuslog summary daily
focuslog summary weekly
focuslog summary daily --date 2026-09-13
```

By default it runs 4 rounds of 25 minute study with 5 minute breaks.
Press Ctrl+C to stop early - any completed study time gets saved. When a round
or break ends you get a terminal bell and a desktop notification (`--quiet`
turns that off).

```bash
focuslog log                 # recent sessions, each with a short id
focuslog delete 3f2a91c0     # remove one by its id

focuslog goal set "OS" 10    # aim for 10 hours of OS a week
focuslog goal list
# OS               [######--------------] 03:00:00 / 10:00:00 (30%)
```

The database is auto-created at `~/.focuslog/focuslog.db`. Use `--db` to pick a
different path:

```bash
focuslog --db ./my_study.db start "Algorithms"
```

If you used the old single-file version, your existing database is upgraded in
place the first time you run this one. `python focuslog.py ...` is now
`focuslog ...` (or `python -m focuslog ...`).

### Terminal UI

```bash
focuslog tui                 # type a subject, press Enter
focuslog tui "Math" --minutes 50
```

| Key | Does |
| --- | --- |
| space | pause / resume (paused time isn't counted) |
| s | stop and save what you've done so far |
| n | start another session |
| q | quit (saves first) |

### Sync between computers

You need a server running somewhere (see [docs/DEPLOY.md](docs/DEPLOY.md), or
`docker compose up` to try it locally).

```bash
focuslog register --server https://your-server.example.com
focuslog sync                # upload new sessions, download the rest
focuslog status              # who you're logged in as, how many sessions are waiting

# on a second computer
focuslog login --server https://your-server.example.com
focuslog sync
```

## How it's put together

```
src/focuslog/
  pomodoro.py    the timer as a state machine: no printing, no sleeping, no database
  cli.py         argparse commands; runs the state machine in a plain terminal
  tui.py         Textual app; runs the same state machine full-screen
  db.py          SQLite connection, schema migrations, session queries
  summary.py     daily/weekly totals, done in SQL
  goals.py       weekly goals and progress
  sync.py        sync client (standard library only)
  notify.py      desktop notifications on Windows, macOS and Linux
  server/        FastAPI + SQLAlchemy sync API (Postgres in production)
```

How it works:

- **The timer is a state machine that gets handed the time.** The CLI and the
  TUI both drive the same `Pomodoro` class, and the tests drive it with a fake
  clock, so a 25 minute round is tested in microseconds.
- **Summaries are SQL queries on an indexed column.** The first version loaded
  every row and filtered in Python. Now SQLite does the filtering and adding up
  (`GROUP BY`), and a test checks the query plan really uses the index.
- **Schema migrations are versioned** with `PRAGMA user_version`, each in its
  own transaction, so old databases upgrade safely.
- **Sync is idempotent.** Sessions get a uuid on the device, the server numbers
  every change, and clients pull "everything after the last number I saw".
  Conflicts are last-write-wins and deletes are tombstones. The details and the
  known limits are in [docs/SYNC.md](docs/SYNC.md).

## Tests

```bash
pip install -e ".[dev]"
pytest                       # 86 tests
ruff check .
```

The tests cover the timer, summaries, migrations (including a database made by
the original script), the API, the TUI (with simulated key presses), and two
"devices" syncing through the real server app. CI runs them on Python 3.10 to
3.13 and runs the server and sync tests a second time against Postgres:

```bash
FOCUSLOG_TEST_DATABASE_URL=postgresql://user:pass@localhost/focuslog_test pytest
```

## Things I want to add in future

- A web dashboard on top of the sync API (study heatmap, time per subject, streaks)
- Insights from the data: which hours of the day I finish sessions vs. give up
- Syncing goals, not just sessions
- Automatic sync after each session when logged in
