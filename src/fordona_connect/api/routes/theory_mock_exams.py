"""Mock exams: browse the catalogue, then take one.

A session pins the exam's questions (shuffled) at start; answers arrive all at once
on submit and are graded server-side. A free learner has a fixed number of starts;
see ``learning/mock_exam_sessions`` for the rules.

The ``/mock-exams/sessions...`` routes are declared before ``/mock-exams/{mockExamId}``
so "sessions" is never read as an exam id.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from fordona_connect.api.deps import CurrentLearner, Locale, ReadRateLimit, Theory, WriteRateLimit
from fordona_connect.api.responses import (
    MOCK_EXAM_DETAIL_ERRORS,
    MOCK_EXAM_RESULT_ERRORS,
    MOCK_EXAM_SESSION_ERRORS,
    MOCK_EXAM_START_ERRORS,
    MOCK_EXAM_SUBMIT_ERRORS,
    READ_ERRORS,
)
from fordona_connect.schemas.common import ID_PATTERN
from fordona_connect.schemas.theory import (
    MockExamDetailOut,
    MockExamHistoryEntryOut,
    MockExamResultOut,
    MockExamSessionOut,
    MockExamSubmitIn,
    MockExamSummaryOut,
    StartMockExamIn,
)

router = APIRouter(tags=["theory"])

MockExamId = Annotated[str, Path(alias="mockExamId", pattern=ID_PATTERN, examples=["1"])]
SessionId = Annotated[UUID, Path(alias="sessionId")]


@router.get(
    "/mock-exams",
    response_model=list[MockExamSummaryOut],
    responses=READ_ERRORS,
    summary="List available mock exams",
)
async def list_mock_exams(
    theory: Theory, _: CurrentLearner, __: ReadRateLimit, locale: Locale
) -> list[MockExamSummaryOut]:
    return await theory.list_mock_exams(locale=locale)


@router.get(
    "/mock-exams/sessions",
    response_model=list[MockExamHistoryEntryOut],
    responses=READ_ERRORS,
    summary="The learner's submitted mock exams, newest first",
)
async def mock_exam_history(
    theory: Theory, _: CurrentLearner, __: ReadRateLimit, locale: Locale
) -> list[MockExamHistoryEntryOut]:
    return await theory.mock_exam_history(locale=locale)


@router.get(
    "/mock-exams/sessions/{sessionId}",
    response_model=MockExamSessionOut,
    responses=MOCK_EXAM_SESSION_ERRORS,
    summary="Resume a mock exam session after the app restarts",
)
async def get_mock_exam_session(
    theory: Theory, _: CurrentLearner, __: ReadRateLimit, locale: Locale, session_id: SessionId
) -> MockExamSessionOut:
    return await theory.get_mock_exam_session(session_id, locale=locale)


@router.post(
    "/mock-exams/sessions/{sessionId}/submission",
    response_model=MockExamResultOut,
    responses=MOCK_EXAM_SUBMIT_ERRORS,
    summary="Submit every answer and get the graded result — a retry returns the same result",
)
async def submit_mock_exam(
    theory: Theory,
    _: CurrentLearner,
    __: WriteRateLimit,
    locale: Locale,
    session_id: SessionId,
    body: MockExamSubmitIn,
) -> MockExamResultOut:
    answers = {answer.seq: answer.selected_option_key for answer in body.answers}
    return await theory.submit_mock_exam(session_id, answers=answers, locale=locale)


@router.get(
    "/mock-exams/sessions/{sessionId}/result",
    response_model=MockExamResultOut,
    responses=MOCK_EXAM_RESULT_ERRORS,
    summary="A submitted session's graded result",
)
async def get_mock_exam_result(
    theory: Theory, _: CurrentLearner, __: ReadRateLimit, locale: Locale, session_id: SessionId
) -> MockExamResultOut:
    return await theory.mock_exam_result(session_id, locale=locale)


@router.get(
    "/mock-exams/{mockExamId}",
    response_model=MockExamDetailOut,
    responses=MOCK_EXAM_DETAIL_ERRORS,
    summary="One mock exam's questions, for preview",
)
async def get_mock_exam(
    theory: Theory,
    _: CurrentLearner,
    __: ReadRateLimit,
    locale: Locale,
    mock_exam_id: MockExamId,
) -> MockExamDetailOut:
    return await theory.mock_exam_detail(mock_exam_id, locale=locale)


@router.post(
    "/mock-exams/{mockExamId}/sessions",
    response_model=MockExamSessionOut,
    responses=MOCK_EXAM_START_ERRORS,
    summary="Start or resume a mock exam — idempotent on requestId",
)
async def start_mock_exam(
    theory: Theory,
    _: CurrentLearner,
    __: WriteRateLimit,
    locale: Locale,
    mock_exam_id: MockExamId,
    body: StartMockExamIn,
) -> MockExamSessionOut:
    return await theory.start_mock_exam(mock_exam_id, request_id=body.request_id, locale=locale)
