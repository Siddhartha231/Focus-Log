"""Full-screen terminal UI: big countdown, progress bar, pause/resume, and
this week's totals and goals next to it. Built with Textual.

Run it with:  focuslog tui            (type a subject and press Enter)
         or:  focuslog tui "Math" --minutes 50
"""

import time
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Digits, Footer, Header, Input, ProgressBar, Static

from focuslog import db, goals, summary
from focuslog.pomodoro import (
    AllDone,
    BreakFinished,
    Phase,
    Pomodoro,
    SessionFinished,
)
from focuslog.summary import format_time


def clock_text(seconds: int) -> str:
    # MM:SS for the big display (minutes can go past 59 for long rounds)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


class FocusApp(App):
    TITLE = "FocusLog"
    # we decide what has focus ourselves (the subject box only when idle)
    AUTO_FOCUS = None

    CSS = """
    #timer { width: 3fr; padding: 1 3; align: center middle; }
    #stats { width: 2fr; padding: 1 2; border-left: solid $primary-darken-2; }
    #phase { text-style: bold; width: 100%; content-align: center middle; }
    #clock { width: 100%; text-align: center; margin: 1 0; }
    #clock.paused { color: $warning; }
    #clock.break { color: $success; }
    #bar { width: 100%; }
    #bar Bar { width: 1fr; }
    #hint { width: 100%; content-align: center middle; color: $text-muted; margin-top: 1; }
    #subject { margin-top: 1; }
    .heading { text-style: bold; margin-bottom: 1; }
    #week { height: auto; max-height: 12; margin-bottom: 1; }
    #goals-heading { margin-top: 1; }
    """

    BINDINGS = [
        ("space", "toggle_pause", "Pause/Resume"),
        ("s", "stop", "Stop and save"),
        ("n", "new_session", "New session"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, conn, subject=None, minutes=25, break_minutes=5, rounds=4,
                 alert=None, clock=time.monotonic):
        super().__init__()
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        if rounds <= 0:
            raise ValueError("rounds must be positive")
        if break_minutes < 0:
            raise ValueError("break minutes can't be negative")
        self.conn = conn
        self.initial_subject = subject
        self.minutes = minutes
        self.break_minutes = break_minutes
        self.rounds = rounds
        self.alert = alert
        self.clock = clock
        self.pomo = None

    # --- layout ---

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="timer"):
                yield Static("", id="phase")
                yield Digits(clock_text(self.minutes * 60), id="clock")
                yield ProgressBar(total=1.0, show_eta=False, show_percentage=False, id="bar")
                yield Static("", id="hint")
                yield Input(placeholder="What are you studying? Type it and press Enter",
                            id="subject")
            with Vertical(id="stats"):
                yield Static("This week", classes="heading")
                yield DataTable(id="week", cursor_type="none")
                yield Static("", id="today")
                yield Static("Goals", classes="heading", id="goals-heading")
                # markup=False because the progress bars contain [ and ]
                yield Static("", id="goals", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#week", DataTable).add_columns("Subject", "Sessions", "Time")
        self.refresh_stats()
        # four ticks a second keeps the display smooth and pause feel instant
        self.set_interval(0.25, self.tick)
        if self.initial_subject:
            self.start_session(self.initial_subject)
        else:
            self.show_idle("Ready when you are.")

    # --- starting and stopping ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        subject = event.value.strip()
        if subject:
            self.start_session(subject)

    def start_session(self, subject: str) -> None:
        self.pomo = Pomodoro(subject, self.minutes * 60, self.break_minutes * 60, self.rounds)
        self.pomo.start(self.clock())
        box = self.query_one("#subject", Input)
        box.value = ""
        box.display = False
        box.disabled = True
        self.set_focus(None)  # so space/s/n reach the app instead of the text box
        self.update_display()

    def show_idle(self, message: str) -> None:
        self.pomo = None
        self.query_one("#phase", Static).update(message)
        clock = self.query_one("#clock", Digits)
        clock.update(clock_text(self.minutes * 60))
        clock.remove_class("paused", "break")
        self.query_one("#bar", ProgressBar).update(progress=0)
        self.query_one("#hint", Static).update(
            "%d rounds of %d min, %d min breaks" % (self.rounds, self.minutes, self.break_minutes))
        box = self.query_one("#subject", Input)
        box.disabled = False
        box.display = True
        box.focus()

    def action_toggle_pause(self) -> None:
        if self.pomo is None:
            return
        if self.pomo.paused:
            self.pomo.resume(self.clock())
        else:
            self.pomo.pause(self.clock())
        self.update_display()

    def action_stop(self) -> None:
        if self.pomo is None:
            return
        self.handle_events(self.pomo.stop(self.clock()))
        self.show_idle("Stopped early. Your time was saved.")

    def action_new_session(self) -> None:
        if self.pomo is None:
            self.query_one("#subject", Input).focus()

    async def action_quit(self) -> None:
        # quitting mid-round still saves the time, same as Ctrl+C in the plain CLI
        if self.pomo is not None:
            self.handle_events(self.pomo.stop(self.clock()))
        self.exit()

    # --- the timer loop ---

    def tick(self) -> None:
        if self.pomo is None:
            return
        events = self.pomo.tick(self.clock())
        self.handle_events(events)
        if self.pomo.phase is Phase.DONE:
            self.show_idle("All rounds done!")
        else:
            self.update_display()

    def handle_events(self, events) -> None:
        for event in events:
            if isinstance(event, SessionFinished):
                db.add_session(self.conn, event.subject, event.started_at,
                               event.seconds, event.status)
                self.refresh_stats()
                if event.status == "completed":
                    self.send_alert("Round %d done: %s" % (event.round_number, event.subject))
            elif isinstance(event, BreakFinished):
                self.send_alert("Break is over, back to work")
            elif isinstance(event, AllDone):
                pass  # tick() switches the screen back to idle

    def send_alert(self, message: str) -> None:
        self.notify(message)  # in-app toast
        if self.alert:
            self.alert("FocusLog", message)  # bell + desktop notification

    # --- drawing ---

    def update_display(self) -> None:
        pomo = self.pomo
        self.query_one("#phase", Static).update(pomo.label)
        clock = self.query_one("#clock", Digits)
        clock.update(clock_text(pomo.remaining))
        clock.set_class(pomo.paused, "paused")
        clock.set_class(pomo.phase is Phase.BREAK, "break")
        self.query_one("#bar", ProgressBar).update(progress=pomo.progress)
        if pomo.paused:
            hint = "Paused. Press space to resume."
        else:
            hint = "space: pause    s: stop and save"
        self.query_one("#hint", Static).update(hint)

    def refresh_stats(self) -> None:
        today = datetime.now().date()
        first_day, last_day = summary.period_bounds("weekly", today)

        table = self.query_one("#week", DataTable)
        table.clear()
        for subject, count, secs in summary.subject_totals(self.conn, first_day, last_day):
            table.add_row(subject, str(count), format_time(secs))

        today_seconds = summary.day_totals(self.conn, today, today).get(today, 0)
        self.query_one("#today", Static).update("Today: " + format_time(today_seconds))

        lines = []
        for g in goals.weekly_progress(self.conn, today):
            lines.append("%s\n%s %s / %s" % (
                g.subject, goals.progress_bar(g.fraction, 12),
                format_time(g.done_seconds), format_time(g.goal_seconds)))
        if lines:
            text = "\n".join(lines)
        else:
            text = 'No goals yet. Try: focuslog goal set "OS" 10'
        self.query_one("#goals", Static).update(text)
