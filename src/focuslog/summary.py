"""Daily and weekly summaries. The filtering and adding-up happens in SQL."""

from datetime import date, timedelta


def format_time(seconds: int) -> str:
    # turn seconds into HH:MM:SS
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return "%02d:%02d:%02d" % (h, m, s)


def period_bounds(period: str, target: date):
    """First and last day (inclusive) of the day or week that contains target."""
    if period == "daily":
        return target, target
    # weekly - start from Monday of that week
    # weekday() returns 0 for Monday, 6 for Sunday
    first_day = target - timedelta(days=target.weekday())
    return first_day, first_day + timedelta(days=6)


def _range_params(first_day: date, last_day: date):
    # started_at is stored as ISO text ("2026-09-13T14:05:00"), which sorts the
    # same way as the dates themselves, so plain text comparison works and can
    # use the index on started_at.
    return first_day.isoformat(), (last_day + timedelta(days=1)).isoformat()


def subject_totals(conn, first_day: date, last_day: date) -> list:
    """[(subject, session_count, total_seconds), ...] sorted by subject."""
    rows = conn.execute(
        """SELECT subject, COUNT(*), SUM(duration_seconds)
           FROM sessions
           WHERE deleted = 0 AND started_at >= ? AND started_at < ?
           GROUP BY subject
           ORDER BY subject""",
        _range_params(first_day, last_day),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def day_totals(conn, first_day: date, last_day: date) -> dict:
    """{date: total_seconds} for days that have at least one session."""
    rows = conn.execute(
        """SELECT substr(started_at, 1, 10), SUM(duration_seconds)
           FROM sessions
           WHERE deleted = 0 AND started_at >= ? AND started_at < ?
           GROUP BY substr(started_at, 1, 10)""",
        _range_params(first_day, last_day),
    ).fetchall()
    return {date.fromisoformat(r[0]): r[1] for r in rows}


def summary_lines(conn, period: str, target: date) -> list:
    """The text of a summary, one string per line."""
    first_day, last_day = period_bounds(period, target)
    lines = []

    if period == "daily":
        lines.append("Daily summary for " + str(first_day))
    else:
        lines.append("Weekly summary: " + str(first_day) + " through " + str(last_day))

    totals = subject_totals(conn, first_day, last_day)
    if len(totals) == 0:
        lines.append("No study sessions recorded for this period.")
        return lines

    lines.append("Subject\t\tSessions\tTime")
    total_sessions = 0
    total_seconds = 0
    for subject, count, secs in totals:
        lines.append(subject + "\t\t" + str(count) + "\t\t" + format_time(secs))
        total_sessions += count
        total_seconds += secs
    lines.append("Total: " + str(total_sessions) + " sessions, " + format_time(total_seconds))

    if period == "weekly":
        by_day = day_totals(conn, first_day, last_day)
        lines.append("")
        lines.append("Daily totals:")
        d = first_day
        while d <= last_day:
            lines.append(str(d) + " (" + d.strftime("%a") + "): " + format_time(by_day.get(d, 0)))
            d += timedelta(days=1)

    return lines
