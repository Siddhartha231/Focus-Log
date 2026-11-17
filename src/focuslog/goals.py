"""Weekly study goals, like "10 hours of OS this week"."""

from dataclasses import dataclass
from datetime import date

from focuslog.summary import period_bounds, subject_totals


@dataclass
class GoalProgress:
    subject: str
    goal_seconds: int
    done_seconds: int

    @property
    def fraction(self) -> float:
        return min(1.0, self.done_seconds / self.goal_seconds)


def set_goal(conn, subject: str, hours: float) -> None:
    if hours <= 0:
        raise ValueError("hours must be positive")
    if subject.strip() == "":
        raise ValueError("subject can't be empty")
    conn.execute(
        "INSERT INTO goals (subject, weekly_seconds) VALUES (?, ?) "
        "ON CONFLICT(subject) DO UPDATE SET weekly_seconds = excluded.weekly_seconds",
        (subject, int(hours * 3600)),
    )


def remove_goal(conn, subject: str) -> bool:
    cur = conn.execute("DELETE FROM goals WHERE subject = ?", (subject,))
    return cur.rowcount > 0


def weekly_progress(conn, today: date) -> list:
    """Progress on every goal for the week that contains `today`."""
    first_day, last_day = period_bounds("weekly", today)
    done = {subject: secs for subject, _count, secs in subject_totals(conn, first_day, last_day)}
    goals = conn.execute("SELECT subject, weekly_seconds FROM goals ORDER BY subject").fetchall()
    return [GoalProgress(g[0], g[1], done.get(g[0], 0)) for g in goals]


def progress_bar(fraction: float, width: int = 20) -> str:
    filled = int(round(fraction * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"
