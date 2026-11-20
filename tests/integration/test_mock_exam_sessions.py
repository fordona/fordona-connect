"""Mock exam sessions against Postgres (testcontainers; skips without Docker).

Content is seeded through the real content sync from a fake Boost catalogue.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fordona_connect.clients.boost.client import BoostClient
from fordona_connect.learning.content.snapshot import SnapshotProvider
from fordona_connect.learning.content.sync import ContentSyncService
from fordona_connect.learning.errors import (
    ItemNotFoundError,
    MockExamAttemptsExhaustedError,
    MockExamNotFoundError,
    MockExamNotSubmittedError,
    MockExamSessionNotFoundError,
    MockExamTimedOutError,
    MockExamUnavailableError,
    OptionInvalidError,
    RequestIdConflictError,
)
from fordona_connect.learning.mock_exam_sessions.models import MockExamItem, MockExamSession
from fordona_connect.learning.mock_exam_sessions.scoring import MockExamRules
from fordona_connect.learning.mock_exam_sessions.service import MockExamSessionService
from fordona_connect.learning.platform import forget_learner_data
from tests.learning_content.boost_world import BASE, LOCALES, BoostWorld, FakeLesson, FakeQuestion

pytestmark = pytest.mark.integration

LEARNER = "kp_learner_1"
OTHER = "kp_learner_2"
EXAM = "1"
SHORT_EXAM = "2"
BROKEN_EXAM = "3"
T0 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
RULES = MockExamRules(
    free_attempts=2, pass_percent=75, time_limit_seconds=600, submit_grace_seconds=30
)


class Subscriptions:
    def __init__(self, subscribed: set[str] | None = None) -> None:
        self.subscribed = subscribed or set()

    async def is_subscribed(self, learner_id: str) -> bool:
        return learner_id in self.subscribed


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def keep_order(values: Sequence[str]) -> list[str]:
    """A deterministic 'shuffle': questions in exam order, options as published."""
    return list(values)


def catalogue() -> BoostWorld:
    return BoostWorld(
        modules=["m1"],
        lessons=[FakeLesson("l1", "m1")],
        questions=[
            FakeQuestion("q1", "l1"),
            FakeQuestion("q2", "l1", correct="b"),
            FakeQuestion("q3", "l1", correct="c"),
            FakeQuestion("q4", "l1"),
        ],
        mock_exams=[
            {
                "id": EXAM,
                "version": "1",
                "questionCount": 4,
                "questionIds": ["q1", "q2", "q3", "q4"],
                "complete": True,
            },
            {
                "id": SHORT_EXAM,
                "version": "1",
                "questionCount": 2,
                "questionIds": ["q1", "q2"],
                "complete": True,
            },
            {
                "id": BROKEN_EXAM,
                "version": "1",
                "questionCount": 4,
                "questionIds": ["q1"],
                "complete": False,
            },
        ],
    )


@pytest.fixture
async def sessions(learning_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    maker = async_sessionmaker(learning_engine, expire_on_commit=False)
    async with httpx.AsyncClient(base_url=BASE) as http:
        sync = ContentSyncService(
            maker,
            BoostClient(http, token="svc", grading_token="grading-token", max_retries=0),
            locales=LOCALES,
            default_locale="sv-SE",
            page_size=50,
        )
        with respx.mock(assert_all_called=False) as router:
            catalogue().mount(router)
            await sync.sync()
    return maker


@pytest.fixture
def clock() -> Clock:
    return Clock()


def service(
    sessions: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    subscribed: set[str] | None = None,
    rules: MockExamRules = RULES,
) -> MockExamSessionService:
    return MockExamSessionService(
        sessions,
        SnapshotProvider(sessions, freshness_seconds=0),
        Subscriptions(subscribed),
        rules=rules,
        now=clock,
        shuffle=keep_order,
    )


# --- start -------------------------------------------------------------------------


async def test_start_pins_every_exam_question_with_its_deadline(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    out = await service(sessions, clock).start(LEARNER, EXAM, uuid.uuid4())

    assert out.status == "IN_PROGRESS"
    assert [(i.seq, i.question_id, i.version) for i in out.items] == [
        (1, "q1", "1"),
        (2, "q2", "1"),
        (3, "q3", "1"),
        (4, "q4", "1"),
    ]
    assert out.items[0].option_order == ["a", "b", "c"]
    assert out.time_limit_seconds == 600
    assert out.expires_at == T0 + timedelta(seconds=600)
    assert out.pass_percent == 75
    assert out.free_attempts_remaining == 1


async def test_start_replays_the_same_request_without_using_another_attempt(
    sessions, clock
) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    request_id = uuid.uuid4()

    first = await svc.start(LEARNER, EXAM, request_id)
    second = await svc.start(LEARNER, EXAM, request_id)

    assert second.session_id == first.session_id
    assert second.free_attempts_remaining == 1


async def test_start_resumes_the_open_session_for_the_same_exam(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)

    first = await svc.start(LEARNER, EXAM, uuid.uuid4())
    second = await svc.start(LEARNER, EXAM, uuid.uuid4())

    assert second.session_id == first.session_id
    assert second.free_attempts_remaining == 1


async def test_start_reusing_a_request_id_for_another_exam_is_a_conflict(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    request_id = uuid.uuid4()
    await svc.start(LEARNER, EXAM, request_id)

    with pytest.raises(RequestIdConflictError):
        await svc.start(LEARNER, SHORT_EXAM, request_id)


async def test_start_unknown_exam_is_not_found(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(MockExamNotFoundError):
        await service(sessions, clock).start(LEARNER, "999", uuid.uuid4())


async def test_start_exam_without_its_full_question_set_is_unavailable(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(MockExamUnavailableError):
        await service(sessions, clock).start(LEARNER, BROKEN_EXAM, uuid.uuid4())


async def test_free_learner_cannot_start_after_using_every_free_attempt(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    await _sit(svc, EXAM)
    await _sit(svc, SHORT_EXAM)

    with pytest.raises(MockExamAttemptsExhaustedError):
        await svc.start(LEARNER, EXAM, uuid.uuid4())


async def test_parallel_starts_cannot_both_use_the_last_free_attempt(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock, rules=MockExamRules(free_attempts=1, time_limit_seconds=600))

    results = await asyncio.gather(
        svc.start(LEARNER, EXAM, uuid.uuid4()),
        svc.start(LEARNER, SHORT_EXAM, uuid.uuid4()),
        return_exceptions=True,
    )

    assert sum(isinstance(r, MockExamAttemptsExhaustedError) for r in results) == 1
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(MockExamSession)
            .where(MockExamSession.learner_id == LEARNER)
        )
    assert count == 1


async def test_subscribed_learner_has_no_attempt_limit(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock, subscribed={LEARNER})
    for _ in range(3):
        await _sit(svc, EXAM)

    out = await svc.start(LEARNER, EXAM, uuid.uuid4())

    assert out.free_attempts_remaining is None


async def test_attempts_used_while_free_still_count_after_subscribing_lapses(
    sessions, clock
) -> None:  # type: ignore[no-untyped-def]
    await _sit(service(sessions, clock, subscribed={LEARNER}), EXAM)  # paid: uses nothing

    access = await service(sessions, clock).access(LEARNER)

    assert access.level == "FREE"
    assert access.mock_exams.free_attempts_remaining == 2


# --- submit --------------------------------------------------------------------------


async def test_submit_grades_every_item_and_leaves_unanswered_wrong(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    clock.now = T0 + timedelta(seconds=125)

    result = await svc.submit(
        LEARNER, uuid.UUID(started.session_id), {1: "a", 2: "b", 3: "a"}, locale="sv-SE"
    )

    assert result.status == "COMPLETED"
    assert [(i.seq, i.selected_option_key, i.is_correct) for i in result.items] == [
        (1, "a", True),
        (2, "b", True),
        (3, "a", False),
        (4, None, False),
    ]
    assert result.items[2].correct_option_key == "c"
    assert result.items[2].correct_option_label == "C"
    assert (result.correct_count, result.total_count, result.score_percent) == (2, 4, 50)
    assert result.passed is False
    assert result.time_taken_seconds == 125


async def test_submit_at_the_pass_mark_passes(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())

    result = await svc.submit(
        LEARNER, uuid.UUID(started.session_id), {1: "a", 2: "b", 3: "c"}, locale="sv-SE"
    )

    assert result.score_percent == 75
    assert result.passed is True


async def test_resubmitting_returns_the_stored_result_without_regrading(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    session_id = uuid.UUID(started.session_id)
    first = await svc.submit(LEARNER, session_id, {1: "a"}, locale="sv-SE")

    again = await svc.submit(LEARNER, session_id, {1: "b", 2: "b", 3: "c", 4: "a"}, locale="sv-SE")

    assert again == first


async def test_submit_within_the_grace_period_is_graded(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    clock.now = T0 + timedelta(seconds=600 + 20)

    result = await svc.submit(LEARNER, uuid.UUID(started.session_id), {1: "a"}, locale="sv-SE")

    assert result.status == "COMPLETED"
    assert result.time_taken_seconds == 600  # grace time is not exam time


async def test_submit_after_the_grace_period_times_the_session_out(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    session_id = uuid.UUID(started.session_id)
    clock.now = T0 + timedelta(seconds=600 + 31)

    with pytest.raises(MockExamTimedOutError):
        await svc.submit(LEARNER, session_id, {1: "a"}, locale="sv-SE")

    assert (await svc.get(LEARNER, session_id)).status == "TIMED_OUT"


async def test_start_after_an_overdue_session_begins_a_new_sitting(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    first = await svc.start(LEARNER, EXAM, uuid.uuid4())
    clock.now = T0 + timedelta(hours=2)

    second = await svc.start(LEARNER, EXAM, uuid.uuid4())

    assert second.session_id != first.session_id
    assert second.free_attempts_remaining == 0
    assert (await svc.get(LEARNER, uuid.UUID(first.session_id))).status == "TIMED_OUT"


async def test_submit_unknown_seq_is_rejected_and_nothing_is_saved(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    session_id = uuid.UUID(started.session_id)

    with pytest.raises(ItemNotFoundError):
        await svc.submit(LEARNER, session_id, {1: "a", 9: "a"}, locale="sv-SE")

    assert (await svc.get(LEARNER, session_id)).status == "IN_PROGRESS"


async def test_submit_invalid_option_is_rejected_and_nothing_is_saved(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())
    session_id = uuid.UUID(started.session_id)

    with pytest.raises(OptionInvalidError):
        await svc.submit(LEARNER, session_id, {1: "a", 2: "z"}, locale="sv-SE")

    async with sessions() as session:
        answered = await session.scalar(
            select(func.count())
            .select_from(MockExamItem)
            .where(MockExamItem.selected_option_key.is_not(None))
        )
    assert answered == 0


async def test_another_learners_session_is_not_found(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())

    with pytest.raises(MockExamSessionNotFoundError):
        await svc.submit(OTHER, uuid.UUID(started.session_id), {}, locale="sv-SE")
    with pytest.raises(MockExamSessionNotFoundError):
        await svc.get(OTHER, uuid.UUID(started.session_id))


# --- result, history, access, deletion ---------------------------------------------


async def test_result_before_submitting_is_a_conflict(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    started = await svc.start(LEARNER, EXAM, uuid.uuid4())

    with pytest.raises(MockExamNotSubmittedError):
        await svc.result(LEARNER, uuid.UUID(started.session_id), locale="sv-SE")


async def test_history_lists_submitted_sessions_newest_first(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock, subscribed={LEARNER})
    first = await _sit(svc, EXAM)
    clock.now = T0 + timedelta(minutes=5)
    second = await _sit(svc, SHORT_EXAM)
    await svc.start(LEARNER, EXAM, uuid.uuid4())  # open, so not history

    history = await svc.history(LEARNER, locale="en-GB")

    assert [h.session_id for h in history] == [second, first]
    assert history[0].name == f"{SHORT_EXAM} en-GB"


async def test_access_reports_level_availability_and_remaining_attempts(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    await svc.start(LEARNER, EXAM, uuid.uuid4())

    access = await svc.access(LEARNER)

    assert access.level == "FREE"
    assert access.mock_exams.available is True
    assert access.mock_exams.free_attempts_limit == 2
    assert access.mock_exams.free_attempts_remaining == 1


async def test_deleting_a_learner_removes_their_mock_exam_sessions(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock)
    await _sit(svc, EXAM)
    await svc.start(OTHER, EXAM, uuid.uuid4())

    async with sessions() as session, session.begin():
        await forget_learner_data(session, LEARNER)

    async with sessions() as session:
        learners = set(await session.scalars(select(MockExamSession.learner_id)))
        item_learners = set(await session.scalars(select(MockExamItem.learner_id)))
    assert learners == {OTHER}
    assert item_learners == {OTHER}


async def test_results_are_completed_sittings_newest_first_with_the_best(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock, subscribed={LEARNER})
    await _sit(svc, EXAM)  # 1 of 4 right: 25 %
    clock.now = T0 + timedelta(minutes=5)
    await _sit(svc, SHORT_EXAM)  # 1 of 2 right: 50 %
    clock.now = T0 + timedelta(minutes=10)
    timed_out = await svc.start(LEARNER, EXAM, uuid.uuid4())
    clock.now = T0 + timedelta(hours=2)
    with pytest.raises(MockExamTimedOutError):
        await svc.submit(LEARNER, uuid.UUID(timed_out.session_id), {}, locale="sv-SE")
    await svc.start(LEARNER, SHORT_EXAM, uuid.uuid4())  # open: not a result

    results = await svc.results(LEARNER, recent=3)

    assert [r.score_percent for r in results.recent] == [50, 25]
    assert results.best is not None
    assert (results.best.score_percent, results.best.total_count) == (50, 2)


async def test_results_return_at_most_the_recent_sittings_asked_for(sessions, clock) -> None:  # type: ignore[no-untyped-def]
    svc = service(sessions, clock, subscribed={LEARNER})
    for minute in range(3):
        clock.now = T0 + timedelta(minutes=minute)
        await _sit(svc, EXAM)

    results = await svc.results(LEARNER, recent=2)

    assert len(results.recent) == 2


async def _sit(svc: MockExamSessionService, exam_id: str) -> str:
    started = await svc.start(LEARNER, exam_id, uuid.uuid4())
    await svc.submit(LEARNER, uuid.UUID(started.session_id), {1: "a"}, locale="sv-SE")
    return started.session_id
