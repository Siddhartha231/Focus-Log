"""Command line interface.

    focuslog start "Math" --minutes 25 --rounds 4
    focuslog summary daily
    focuslog summary weekly
    focuslog tui
"""

import argparse
import getpass
import platform
import sys
import time
from datetime import datetime

from focuslog import __version__, config, db, goals, notify, summary
from focuslog import sync as sync_client
from focuslog.pomodoro import (
    AllDone,
    BreakFinished,
    BreakInterrupted,
    Phase,
    Pomodoro,
    SessionFinished,
)
from focuslog.summary import format_time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="focuslog", description="Pomodoro study tracker")
    parser.add_argument("--db", default=None, help="path to the database file")
    parser.add_argument("--version", action="version", version="focuslog " + __version__)
    subparsers = parser.add_subparsers(dest="command")

    # start command
    start_p = subparsers.add_parser("start", help="start a study session")
    _add_timer_options(start_p)
    start_p.add_argument("--quiet", action="store_true", help="no bell or desktop notification")

    # summary command
    sum_p = subparsers.add_parser("summary", help="show study summary")
    sum_p.add_argument("period", choices=["daily", "weekly"])
    sum_p.add_argument("--date", default=None, help="date in YYYY-MM-DD format")

    # log / delete
    log_p = subparsers.add_parser("log", help="list recent sessions")
    log_p.add_argument("--limit", type=int, default=10)
    del_p = subparsers.add_parser("delete", help="delete a session by the id shown in 'log'")
    del_p.add_argument("id")

    # goals
    goal_p = subparsers.add_parser("goal", help="weekly goals per subject")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)
    goal_set = goal_sub.add_parser("set", help='for example: goal set "OS" 10')
    goal_set.add_argument("subject")
    goal_set.add_argument("hours", type=float, help="hours per week")
    goal_rm = goal_sub.add_parser("remove")
    goal_rm.add_argument("subject")
    goal_sub.add_parser("list", help="show progress for this week")

    # full-screen terminal UI
    tui_p = subparsers.add_parser(
        "tui", help="full-screen timer (needs: pip install focuslog[tui])")
    _add_timer_options(tui_p, subject_optional=True)
    tui_p.add_argument("--quiet", action="store_true", help="no bell or desktop notification")

    # sync
    for name in ("register", "login"):
        p = subparsers.add_parser(name, help=name + " on a sync server")
        p.add_argument("--server", default=None, help="for example https://focuslog.example.com")
        p.add_argument("--email", default=None)
    subparsers.add_parser("logout", help="forget the login on this device")
    subparsers.add_parser("sync", help="upload local sessions and download new ones")
    subparsers.add_parser("status", help="show login and how many sessions are waiting to sync")

    return parser


def _add_timer_options(p, subject_optional=False):
    if subject_optional:
        p.add_argument("subject", nargs="?", default=None)
    else:
        p.add_argument("subject")
    p.add_argument("--minutes", type=int, default=25)
    p.add_argument("--break-minutes", type=int, default=5)
    p.add_argument("--rounds", type=int, default=4)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def run_timer(conn, pomo, out=print, clock=time.monotonic, sleep=time.sleep, alert=None) -> None:
    """Run the Pomodoro in the terminal until it finishes or the user hits Ctrl+C."""
    pomo.start(clock())
    try:
        while pomo.phase is not Phase.DONE:
            label = pomo.label
            events = pomo.tick(clock())
            if events:
                # the phase that just ended reached zero
                out("\r" + label + " | " + format_time(0) + " left   ")
                _handle_events(conn, events, out, alert)
            else:
                out("\r" + label + " | " + format_time(pomo.remaining) + " left   ",
                    end="", flush=True)
                sleep(1)
    except KeyboardInterrupt:
        out()
        _handle_events(conn, pomo.stop(clock()), out, alert=None)


def _handle_events(conn, events, out, alert) -> None:
    for event in events:
        if isinstance(event, SessionFinished):
            db.add_session(conn, event.subject, event.started_at, event.seconds, event.status)
            out("Saved " + format_time(event.seconds) + " for " + event.subject + ".")
            if event.status == "interrupted":
                out("Stopped early.")
            elif alert:
                alert("FocusLog", "Round %d done: %s" % (event.round_number, event.subject))
        elif isinstance(event, BreakFinished):
            if alert:
                alert("FocusLog", "Break is over, back to work")
        elif isinstance(event, BreakInterrupted):
            out("Break interrupted, quitting.")
        elif isinstance(event, AllDone):
            out("All rounds done!")


def start_command(conn, args) -> int:
    try:
        pomo = Pomodoro(args.subject, args.minutes * 60, args.break_minutes * 60, args.rounds)
    except ValueError as err:
        print("Error: " + str(err))
        return 2
    run_timer(conn, pomo, alert=None if args.quiet else notify.notify)
    return 0


# ---------------------------------------------------------------------------
# summary, log, delete, goals
# ---------------------------------------------------------------------------

def summary_command(conn, args) -> int:
    # figure out what date the user wants
    if args.date is None:
        target = datetime.now().date()
    else:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("Error: date must look like 2026-09-13")
            return 2
    for line in summary.summary_lines(conn, args.period, target):
        print(line)
    return 0


def log_command(conn, args) -> int:
    rows = db.recent_sessions(conn, args.limit)
    if not rows:
        print("No sessions yet.")
        return 0
    for r in rows:
        # the first 8 characters of the uuid are enough to pick a session
        print("%s  %s  %s  %-11s  %s" % (
            r["uuid"][:8], r["started_at"][:16].replace("T", " "),
            format_time(r["duration_seconds"]), r["status"], r["subject"]))
    return 0


def delete_command(conn, args) -> int:
    matches = db.find_by_prefix(conn, args.id)
    if len(matches) == 0:
        print("Error: no session with an id starting with " + args.id)
        return 1
    if len(matches) > 1:
        print("Error: %d sessions match %s, type more of the id" % (len(matches), args.id))
        return 1
    row = matches[0]
    db.delete_session(conn, row["uuid"])
    print("Deleted %s (%s, %s)." % (
        row["uuid"][:8], row["subject"], format_time(row["duration_seconds"])))
    return 0


def goal_command(conn, args) -> int:
    if args.goal_command == "set":
        try:
            goals.set_goal(conn, args.subject, args.hours)
        except ValueError as err:
            print("Error: " + str(err))
            return 2
        print("Goal set: %s, %g hours a week." % (args.subject, args.hours))
    elif args.goal_command == "remove":
        if not goals.remove_goal(conn, args.subject):
            print("Error: no goal for " + args.subject)
            return 1
        print("Removed the goal for " + args.subject + ".")
    else:
        progress = goals.weekly_progress(conn, datetime.now().date())
        if not progress:
            print('No goals yet. Add one with: focuslog goal set "OS" 10')
            return 0
        for g in progress:
            print("%-16s %s %s / %s (%d%%)" % (
                g.subject, goals.progress_bar(g.fraction), format_time(g.done_seconds),
                format_time(g.goal_seconds), int(g.fraction * 100)))
    return 0


def tui_command(conn, args) -> int:
    try:
        from focuslog.tui import FocusApp
    except ImportError:
        print("Error: the terminal UI needs Textual. Install it with: pip install focuslog[tui]")
        return 1
    try:
        app = FocusApp(conn, subject=args.subject, minutes=args.minutes,
                       break_minutes=args.break_minutes, rounds=args.rounds,
                       alert=None if args.quiet else notify.notify)
    except ValueError as err:
        print("Error: " + str(err))
        return 2
    app.run()
    return 0


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def _transport(server_url):
    return sync_client.HttpTransport(server_url)


def _ask_account(args, cfg):
    server = args.server or cfg.get("server_url") or input("Server URL: ").strip()
    if not server.startswith(("http://", "https://")):
        server = "https://" + server
    email = args.email or input("Email: ").strip()
    password = getpass.getpass("Password: ")
    return server.rstrip("/"), email.strip().lower(), password


def register_command(conn, args, transport_factory=_transport) -> int:
    server, email, password = _ask_account(args, config.load())
    transport = transport_factory(server)
    sync_client.register(transport, email, password)
    print("Account created.")
    return _finish_login(conn, transport, server, email, password)


def login_command(conn, args, transport_factory=_transport) -> int:
    server, email, password = _ask_account(args, config.load())
    return _finish_login(conn, transport_factory(server), server, email, password)


def _finish_login(conn, transport, server, email, password) -> int:
    token = sync_client.login(transport, email, password, platform.node() or "unknown device")
    config.save({"server_url": server, "email": email, "token": token})
    sync_client.bind_account(conn, email + "@" + server)
    print("Logged in as %s. Run 'focuslog sync' to sync." % email)
    return 0


def logout_command(conn, args, transport_factory=_transport) -> int:
    cfg = config.load()
    if "token" not in cfg:
        print("Not logged in.")
        return 0
    try:
        # tell the server to throw the token away too
        sync_client.logout(transport_factory(cfg["server_url"]), cfg["token"])
    except sync_client.SyncError:
        pass  # offline is fine, we still forget it locally
    config.clear()
    print("Logged out.")
    return 0


def sync_command(conn, args, transport_factory=_transport) -> int:
    cfg = config.load()
    if "token" not in cfg:
        print("Error: not logged in. Run: focuslog login --server <url>")
        return 1
    result = sync_client.sync(conn, transport_factory(cfg["server_url"]), cfg["token"])
    print("Synced: %d uploaded, %d downloaded." % (result.pushed, result.pulled))
    return 0


def status_command(conn, args) -> int:
    cfg = config.load()
    if "token" in cfg:
        print("Logged in as %s on %s" % (cfg["email"], cfg["server_url"]))
    else:
        print("Not logged in (sessions are only saved on this device).")
    print("%d session(s) waiting to sync." % sync_client.pending_count(conn))
    return 0


# ---------------------------------------------------------------------------

COMMANDS = {
    "start": start_command,
    "summary": summary_command,
    "log": log_command,
    "delete": delete_command,
    "goal": goal_command,
    "tui": tui_command,
    "register": register_command,
    "login": login_command,
    "logout": logout_command,
    "sync": sync_command,
    "status": status_command,
}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    conn = db.connect(args.db)
    try:
        return COMMANDS[args.command](conn, args)
    except sync_client.SyncError as err:
        print("Error: " + str(err))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
