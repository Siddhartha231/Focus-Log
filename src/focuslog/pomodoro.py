"""The Pomodoro timer as a small state machine.

It knows nothing about printing, sleeping or the database. The caller feeds it
the current time with tick() and gets back a list of events (a study round
finished, a break finished, everything is done). Because of that, the plain
CLI and the full-screen terminal UI both run on this one class, and tests can
drive it with a fake clock instead of waiting 25 real minutes.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Phase(Enum):
    STUDY = "study"
    BREAK = "break"
    DONE = "done"


# --- events returned by tick() and stop() ---

@dataclass
class SessionFinished:
    """A study round ended (fully, or early because the user stopped)."""
    subject: str
    started_at: datetime
    seconds: int
    status: str  # "completed" or "interrupted"
    round_number: int


@dataclass
class BreakFinished:
    round_number: int


@dataclass
class BreakInterrupted:
    round_number: int


@dataclass
class AllDone:
    pass


class Pomodoro:
    def __init__(self, subject, study_seconds, break_seconds, rounds, wall_clock=datetime.now):
        if study_seconds <= 0:
            raise ValueError("minutes must be positive")
        if rounds <= 0:
            raise ValueError("rounds must be positive")
        if break_seconds < 0:
            raise ValueError("break minutes can't be negative")
        if subject.strip() == "":
            raise ValueError("subject can't be empty")

        self.subject = subject
        self.study_seconds = study_seconds
        self.break_seconds = break_seconds
        self.rounds = rounds
        self.wall_clock = wall_clock  # only used to timestamp sessions

        self.round_number = 1
        self.phase = Phase.STUDY
        self.elapsed = 0.0          # seconds spent in the current phase, not counting pauses
        self.paused = False
        self.phase_started_at = None
        self._last_tick = None

    # --- reading the state ---

    @property
    def phase_length(self) -> int:
        if self.phase is Phase.BREAK:
            return self.break_seconds
        return self.study_seconds

    @property
    def remaining(self) -> int:
        if self.phase is Phase.DONE:
            return 0
        return max(0, self.phase_length - int(self.elapsed))

    @property
    def progress(self) -> float:
        """How far through the current phase we are, from 0.0 to 1.0."""
        if self.phase is Phase.DONE:
            return 1.0
        return min(1.0, self.elapsed / self.phase_length)

    @property
    def label(self) -> str:
        if self.phase is Phase.BREAK:
            return "Break %d/%d" % (self.round_number, self.rounds)
        return "Study %d/%d: %s" % (self.round_number, self.rounds, self.subject)

    # --- driving the timer ---

    def start(self, now: float) -> None:
        self._begin_phase(Phase.STUDY, now)

    def tick(self, now: float) -> list:
        """Advance to time `now` (a monotonic clock reading) and return what happened."""
        if self.phase is Phase.DONE or self.paused:
            return []

        self.elapsed += now - self._last_tick
        self._last_tick = now
        if self.elapsed < self.phase_length:
            return []

        # the current phase just finished
        events = []
        if self.phase is Phase.STUDY:
            events.append(SessionFinished(self.subject, self.phase_started_at,
                                          self.study_seconds, "completed", self.round_number))
            if self.round_number == self.rounds:
                self.phase = Phase.DONE
                events.append(AllDone())
            elif self.break_seconds > 0:
                self._begin_phase(Phase.BREAK, now)
            else:
                self.round_number += 1
                self._begin_phase(Phase.STUDY, now)
        else:
            events.append(BreakFinished(self.round_number))
            self.round_number += 1
            self._begin_phase(Phase.STUDY, now)
        return events

    def pause(self, now: float) -> None:
        if self.phase is Phase.DONE or self.paused:
            return
        # count the time up to this moment, then stop counting
        self.elapsed = min(self.phase_length, self.elapsed + (now - self._last_tick))
        self.paused = True

    def resume(self, now: float) -> None:
        if not self.paused:
            return
        self.paused = False
        self._last_tick = now

    def stop(self, now: float) -> list:
        """Stop early (Ctrl+C). Study time done so far still counts."""
        if self.phase is Phase.DONE:
            return []
        if not self.paused:
            self.elapsed = min(self.phase_length, self.elapsed + (now - self._last_tick))

        events = []
        if self.phase is Phase.STUDY:
            seconds = int(self.elapsed)
            # don't save sessions that were basically zero
            if seconds > 0:
                events.append(SessionFinished(self.subject, self.phase_started_at,
                                              seconds, "interrupted", self.round_number))
        else:
            events.append(BreakInterrupted(self.round_number))
        self.phase = Phase.DONE
        return events

    def _begin_phase(self, phase: Phase, now: float) -> None:
        self.phase = phase
        self.elapsed = 0.0
        self._last_tick = now
        self.phase_started_at = self.wall_clock()
