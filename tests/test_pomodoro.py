from datetime import datetime

import pytest

from focuslog.pomodoro import (
    AllDone,
    BreakFinished,
    BreakInterrupted,
    Phase,
    Pomodoro,
    SessionFinished,
)


def make(clock, study=60, rest=10, rounds=2):
    pomo = Pomodoro("Math", study, rest, rounds, wall_clock=lambda: datetime(2026, 9, 13, 10, 0))
    pomo.start(clock())
    return pomo


def test_counts_down(clock):
    pomo = make(clock)
    clock.advance(15)
    assert pomo.tick(clock()) == []
    assert pomo.remaining == 45
    assert pomo.progress == pytest.approx(0.25)
    assert pomo.label == "Study 1/2: Math"


def test_full_run_study_break_study(clock):
    pomo = make(clock)

    clock.advance(60)
    events = pomo.tick(clock())
    assert events == [SessionFinished("Math", datetime(2026, 9, 13, 10, 0), 60, "completed", 1)]
    assert pomo.phase is Phase.BREAK
    assert pomo.label == "Break 1/2"

    clock.advance(10)
    assert pomo.tick(clock()) == [BreakFinished(1)]
    assert pomo.phase is Phase.STUDY and pomo.round_number == 2

    clock.advance(60)
    events = pomo.tick(clock())
    assert isinstance(events[0], SessionFinished) and events[0].round_number == 2
    assert isinstance(events[1], AllDone)  # no break after the last round
    assert pomo.phase is Phase.DONE
    assert pomo.tick(clock()) == []


def test_zero_break_goes_straight_to_next_round(clock):
    pomo = make(clock, rest=0)
    clock.advance(60)
    pomo.tick(clock())
    assert pomo.phase is Phase.STUDY and pomo.round_number == 2


def test_pause_does_not_count_time(clock):
    pomo = make(clock)
    clock.advance(20)
    pomo.pause(clock())
    clock.advance(500)                    # a long lunch
    assert pomo.tick(clock()) == []
    assert pomo.remaining == 40
    pomo.resume(clock())
    clock.advance(10)
    pomo.tick(clock())
    assert pomo.remaining == 30


def test_stop_saves_partial_study_time(clock):
    pomo = make(clock)
    clock.advance(42)
    events = pomo.stop(clock())
    assert events == [SessionFinished("Math", datetime(2026, 9, 13, 10, 0), 42, "interrupted", 1)]
    assert pomo.phase is Phase.DONE


def test_stop_while_paused_excludes_the_pause(clock):
    pomo = make(clock)
    clock.advance(20)
    pomo.pause(clock())
    clock.advance(300)
    events = pomo.stop(clock())
    assert events[0].seconds == 20


def test_stop_right_away_saves_nothing(clock):
    pomo = make(clock)
    assert pomo.stop(clock()) == []


def test_stop_during_break(clock):
    pomo = make(clock)
    clock.advance(60)
    pomo.tick(clock())
    clock.advance(3)
    assert pomo.stop(clock()) == [BreakInterrupted(1)]


@pytest.mark.parametrize("kwargs, message", [
    (dict(study_seconds=0), "minutes must be positive"),
    (dict(rounds=0), "rounds must be positive"),
    (dict(break_seconds=-1), "break minutes can't be negative"),
    (dict(subject="   "), "subject can't be empty"),
])
def test_rejects_bad_settings(kwargs, message):
    settings = dict(subject="Math", study_seconds=60, break_seconds=10, rounds=1)
    settings.update(kwargs)
    with pytest.raises(ValueError, match=message):
        Pomodoro(**settings)
