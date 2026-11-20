"""The single readiness score's arithmetic."""

from fordona_connect.learning_journey import readiness


def test_journey_is_the_mean_best_score_with_untried_challenges_as_zero() -> None:
    assert readiness.journey_percent([100.0, 90.0, 80.0, 80.0, 70.0, None], 10) == 42.0


def test_journey_with_no_challenges_is_zero() -> None:
    assert readiness.journey_percent([], 0) == 0.0


def test_mock_exams_average_the_window_with_missing_sittings_as_zero() -> None:
    assert readiness.mock_exam_percent([70.0, 80.0], 3) == 50.0


def test_mock_exams_ignore_sittings_older_than_the_window() -> None:
    assert readiness.mock_exam_percent([60.0, 60.0, 60.0, 100.0], 3) == 60.0


def test_overall_weighs_mock_exams_by_the_given_weight() -> None:
    assert readiness.overall_percent(42.0, 50.0, 0.6) == 46.8
