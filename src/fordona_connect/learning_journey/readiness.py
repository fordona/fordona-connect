"""The learner's single readiness score: the journey and mock exams, mock exams weighted higher.

    readiness = (1 - w) * journey + w * mock exams            (w = mock_exam_weight)

* **journey**: each challenge counts by its best full-attempt score; a challenge never
  tried counts 0. The mean over every challenge of the journey (the theory lessons'
  "sum of best ÷ number of items" formula, applied to challenges).
* **mock exams**: the mean of the last ``mock_exam_sittings`` completed sittings, a
  missing sitting counting 0, so one lucky exam cannot make a learner look ready. Recent
  sittings rather than best per exam, because Boost's mock exams are fixed question sets
  that a retake can memorise. Timed-out sittings are not results and are not counted.

Every percentage is 0-100, rounded to two decimals.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessRules:
    mock_exam_weight: float = 0.6
    mock_exam_sittings: int = 3


def journey_percent(best_scores: Iterable[float | None], total_challenges: int) -> float:
    if total_challenges <= 0:
        return 0.0
    return round(sum(score or 0.0 for score in best_scores) / total_challenges, 2)


def mock_exam_percent(recent_scores: Sequence[float], sittings: int) -> float:
    """``recent_scores`` newest first; only the first ``sittings`` count."""
    if sittings <= 0:
        return 0.0
    return round(sum(recent_scores[:sittings]) / sittings, 2)


def overall_percent(journey: float, mock_exams: float, mock_exam_weight: float) -> float:
    return round((1 - mock_exam_weight) * journey + mock_exam_weight * mock_exams, 2)
