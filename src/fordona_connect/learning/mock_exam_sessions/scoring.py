"""Pure mock exam rules: score, pass, time taken, free attempts left."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MockExamRules:
    """Settings resolved once at startup; each session copies what it needs at start."""

    free_attempts: int = 3
    pass_percent: int = 75
    # 0 means untimed.
    time_limit_seconds: int = 3600
    # How long after the deadline a submission is still graded (network slack).
    submit_grace_seconds: int = 60


def score_percent(correct: int, total: int) -> int:
    """Whole percent, rounded down (the engine's integer division)."""
    if total <= 0:
        return 0
    return correct * 100 // total


def passed(score: int, pass_percent: int) -> bool:
    return score >= pass_percent


def time_taken_seconds(
    started_at: datetime, finished_at: datetime, expires_at: datetime | None
) -> int:
    """Seconds from start to finish, capped at the deadline: grace time is not exam time."""
    end = min(finished_at, expires_at) if expires_at is not None else finished_at
    return max(0, int((end - started_at).total_seconds()))


def free_attempts_remaining(limit: int, used: int) -> int:
    return max(0, limit - used)
