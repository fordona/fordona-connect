"""Learning errors, with the engine's error codes so clients see no change."""

from fordona_connect.core.errors import AppError, TheoryLessonLockedError, TheoryModuleLockedError

__all__ = [
    "AttemptNotFoundError",
    "ItemNotFoundError",
    "LessonNotFoundError",
    "MockExamAttemptsExhaustedError",
    "MockExamNotFoundError",
    "MockExamNotSubmittedError",
    "MockExamSessionNotFoundError",
    "MockExamTimedOutError",
    "MockExamUnavailableError",
    "OptionInvalidError",
    "PhaseClosedError",
    "PhaseIncompleteError",
    "RequestIdConflictError",
    "ResponseConflictError",
    "TheoryLessonLockedError",
    "TheoryModuleLockedError",
]


class LessonNotFoundError(AppError):
    status_code = 404
    error_code = "THEORY_ATTEMPT_LESSON_NOT_FOUND"
    type_path = "theory/lesson-not-found"


class AttemptNotFoundError(AppError):
    status_code = 404
    error_code = "THEORY_ATTEMPT_NOT_FOUND"
    type_path = "theory/attempt-not-found"


class ItemNotFoundError(AppError):
    status_code = 404
    error_code = "THEORY_ITEM_NOT_FOUND"
    type_path = "theory/item-not-found"


class RequestIdConflictError(AppError):
    status_code = 409
    error_code = "THEORY_REQUEST_ID_CONFLICT"
    type_path = "theory/request-id-conflict"


class ResponseConflictError(AppError):
    status_code = 409
    error_code = "THEORY_RESPONSE_CONFLICT"
    type_path = "theory/response-conflict"


class PhaseClosedError(AppError):
    status_code = 409
    error_code = "THEORY_PHASE_CLOSED"
    type_path = "theory/phase-closed"


class PhaseIncompleteError(AppError):
    status_code = 409
    error_code = "THEORY_PHASE_INCOMPLETE"
    type_path = "theory/phase-incomplete"


class OptionInvalidError(AppError):
    status_code = 422
    error_code = "THEORY_OPTION_INVALID"
    type_path = "theory/option-invalid"


class MockExamNotFoundError(AppError):
    status_code = 404
    error_code = "THEORY_MOCK_EXAM_NOT_FOUND"
    type_path = "theory/mock-exam-not-found"


class MockExamUnavailableError(AppError):
    """The exam exists but Boost could not give it its full question set."""

    status_code = 409
    error_code = "THEORY_MOCK_EXAM_UNAVAILABLE"
    type_path = "theory/mock-exam-unavailable"


class MockExamSessionNotFoundError(AppError):
    status_code = 404
    error_code = "THEORY_MOCK_EXAM_SESSION_NOT_FOUND"
    type_path = "theory/mock-exam-session-not-found"


class MockExamAttemptsExhaustedError(AppError):
    """A free learner has used every free mock exam attempt (the engine's code)."""

    status_code = 403
    error_code = "THEORY_EXAM_ATTEMPTS_EXHAUSTED"
    type_path = "theory/exam-attempts-exhausted"
    default_detail = "All free mock exam attempts are used. Subscribe for unlimited attempts."


class MockExamTimedOutError(AppError):
    status_code = 409
    error_code = "THEORY_EXAM_TIMED_OUT"
    type_path = "theory/exam-timed-out"
    default_detail = "The mock exam's time limit has passed."


class MockExamNotSubmittedError(AppError):
    status_code = 409
    error_code = "THEORY_MOCK_EXAM_NOT_SUBMITTED"
    type_path = "theory/mock-exam-not-submitted"
