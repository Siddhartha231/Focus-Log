# FocusLog

A command-line Pomodoro study tracker using Python and SQLite. Requires Python 3.9+.

## Usage

```bash
python focuslog.py start "Data Structures"
python focuslog.py start "OS" --minutes 50 --break-minutes 10 --rounds 2
python focuslog.py start "Math" --rounds 1
python focuslog.py summary daily
python focuslog.py summary weekly
python focuslog.py summary daily --date 2026-09-13
```

By default it runs 4 rounds of 25 minute study with 5 minute breaks.
Press Ctrl+C to stop early - any completed study time gets saved.

The database is auto-created at `~/.focuslog/focuslog.db`. Use `--db` to
pick a different path:

```bash
python focuslog.py --db ./my_study.db start "Algorithms"
```
