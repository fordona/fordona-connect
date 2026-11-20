"""Pure mock exam rules."""

from datetime import UTC, datetime, timedelta

from fordona_connect.learning.mock_exam_sessions import scoring

T0 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def test_score_rounds_down_to_a_whole_percent() -> None:
    assert scoring.score_percent(2, 3) == 66


def test_score_of_an_empty_exam_is_zero() -> None:
    assert scoring.score_percent(0, 0) == 0


def test_score_at_the_pass_mark_passes() -> None:
    assert scoring.passed(75, 75) is True


def test_score_below_the_pass_mark_fails() -> None:
    assert scoring.passed(74, 75) is False


def test_time_taken_stops_at_the_deadline() -> None:
    expires = T0 + timedelta(seconds=600)

    assert scoring.time_taken_seconds(T0, expires + timedelta(seconds=30), expires) == 600


def test_time_taken_without_a_deadline_is_start_to_finish() -> None:
    assert scoring.time_taken_seconds(T0, T0 + timedelta(seconds=95), None) == 95


def test_free_attempts_remaining_never_goes_negative() -> None:
    assert scoring.free_attempts_remaining(3, 5) == 0
