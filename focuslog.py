# FocusLog - Pomodoro study timer with a database
# Usage: python focuslog.py start "Math" --minutes 25 --rounds 4
#        python focuslog.py summary daily
#        python focuslog.py summary weekly

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta


DB_FILE = os.path.expanduser("~/.focuslog/focuslog.db")


def get_connection():
    # make the folder if it doesn't exist yet
    folder = os.path.dirname(DB_FILE)
    if not os.path.exists(folder):
        os.makedirs(folder)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    subject TEXT,
                    started_at TEXT,
                    duration_seconds INTEGER,
                    status TEXT
                )""")
    conn.commit()
    return conn


def format_time(seconds):
    # turn seconds into HH:MM:SS
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return "%02d:%02d:%02d" % (h, m, s)


def run_timer(total_seconds, label):
    # runs a countdown and prints the time every second
    # returns how many seconds actually elapsed and whether the user hit Ctrl+C
    start = time.time()
    try:
        while True:
            elapsed = int(time.time() - start)
            remaining = total_seconds - elapsed
            if remaining < 0:
                remaining = 0
            print("\r" + label + " | " + format_time(remaining) + " left   ", end="", flush=True)
            if remaining == 0:
                print()
                return total_seconds, False
            time.sleep(1)
    except KeyboardInterrupt:
        elapsed = int(time.time() - start)
        if elapsed > total_seconds:
            elapsed = total_seconds
        print()
        return elapsed, True


def start_command(conn, subject, minutes, break_minutes, rounds):
    for i in range(rounds):
        round_num = i + 1
        started_at = datetime.now()

        label = "Study " + str(round_num) + "/" + str(rounds) + ": " + subject
        elapsed, was_interrupted = run_timer(minutes * 60, label)

        # save what we did (unless it was basically zero)
        if elapsed > 0:
            if was_interrupted:
                status = "interrupted"
            else:
                status = "completed"

            c = conn.cursor()
            c.execute(
                "INSERT INTO sessions (subject, started_at, duration_seconds, status) VALUES (?, ?, ?, ?)",
                (subject, started_at.isoformat(), elapsed, status)
            )
            conn.commit()
            print("Saved " + format_time(elapsed) + " for " + subject + ".")

        if was_interrupted:
            print("Stopped early.")
            return

        # break time (skip the break after the last round)
        if round_num < rounds and break_minutes > 0:
            break_label = "Break " + str(round_num) + "/" + str(rounds)
            _, was_interrupted = run_timer(break_minutes * 60, break_label)
            if was_interrupted:
                print("Break interrupted, quitting.")
                return

    print("All rounds done!")


def summary_command(conn, period, date_str):
    # figure out what date the user wants
    if date_str is None:
        target = datetime.now().date()
    else:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()

    if period == "daily":
        first_day = target
        last_day = target
    else:
        # weekly - start from Monday of that week
        # weekday() returns 0 for Monday, 6 for Sunday
        first_day = target - timedelta(days=target.weekday())
        last_day = first_day + timedelta(days=6)

    # grab everything and filter in Python (there aren't that many rows)
    c = conn.cursor()
    c.execute("SELECT subject, started_at, duration_seconds FROM sessions")
    rows = c.fetchall()

    subject_totals = {}   # subject -> [session_count, total_seconds]
    day_totals = {}       # date -> total_seconds
    for subject, started_at, seconds in rows:
        d = datetime.fromisoformat(started_at).date()
        if d < first_day or d > last_day:
            continue
        if subject not in subject_totals:
            subject_totals[subject] = [0, 0]
        subject_totals[subject][0] += 1
        subject_totals[subject][1] += seconds
        if d in day_totals:
            day_totals[d] += seconds
        else:
            day_totals[d] = seconds

    # print it out
    if period == "daily":
        print("Daily summary for " + str(first_day))
    else:
        print("Weekly summary: " + str(first_day) + " through " + str(last_day))

    if len(subject_totals) == 0:
        print("No study sessions recorded for this period.")
        return

    print("Subject\t\tSessions\tTime")
    total_sessions = 0
    total_seconds = 0
    for subject in sorted(subject_totals.keys()):
        count = subject_totals[subject][0]
        secs = subject_totals[subject][1]
        print(subject + "\t\t" + str(count) + "\t\t" + format_time(secs))
        total_sessions += count
        total_seconds += secs

    print("Total: " + str(total_sessions) + " sessions, " + format_time(total_seconds))

    if period == "weekly":
        print("")
        print("Daily totals:")
        d = first_day
        while d <= last_day:
            secs = day_totals.get(d, 0)
            print(str(d) + " (" + d.strftime("%a") + "): " + format_time(secs))
            d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="Pomodoro study tracker")
    subparsers = parser.add_subparsers(dest="command")

    # start command
    start_p = subparsers.add_parser("start", help="start a study session")
    start_p.add_argument("subject")
    start_p.add_argument("--minutes", type=int, default=25)
    start_p.add_argument("--break-minutes", type=int, default=5)
    start_p.add_argument("--rounds", type=int, default=4)

    # summary command
    sum_p = subparsers.add_parser("summary", help="show study summary")
    sum_p.add_argument("period", choices=["daily", "weekly"])
    sum_p.add_argument("--date", default=None, help="date in YYYY-MM-DD format")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # basic input checks
    if args.command == "start":
        if args.minutes <= 0:
            print("Error: minutes must be positive")
            return
        if args.rounds <= 0:
            print("Error: rounds must be positive")
            return
        if args.break_minutes < 0:
            print("Error: break minutes can't be negative")
            return
        if args.subject.strip() == "":
            print("Error: subject can't be empty")
            return

    conn = get_connection()
    try:
        if args.command == "start":
            start_command(conn, args.subject, args.minutes, args.break_minutes, args.rounds)
        elif args.command == "summary":
            summary_command(conn, args.period, args.date)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
