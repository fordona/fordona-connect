"""Taking a mock exam: start (or resume), submit for grading, result, history, access.

A mock exam is one of Boost's fixed exams (``content_mock_exam``). A session pins
each question to its current revision and shuffles question and option order with
a CSPRNG. Answers arrive all at once on submit, as in the engine's mock exams; the
learner gets no feedback until then.

Rules ported from the engine's ``MockExamService``:

* A free learner has ``free_attempts`` starts in total (a lifetime count, used at
  start). A subscribed learner has no limit. Resuming an open session, or replaying
  a start's ``requestId``, uses nothing. If fordona-engine cannot say whether the
  learner is subscribed, start refuses (503) rather than spend a free attempt on a
  guess; every other read fails closed.
* The pass mark is ``pass_percent`` of the questions, rounded down.
* A deadline (``time_limit_seconds`` after start) is enforced at submit, with
  ``submit_grace_seconds`` of network slack. Past that the session is ``TIMED_OUT``.

Concurrency:

* **start** takes a per-learner advisory lock while it counts free attempts, so
  two parallel starts cannot both use the last one. Two unique indexes (one open
  session per learner and exam; one session per request id) arbitrate a double
  start, and the loser returns the winner's session.
* **submit** locks the session row. A repeated submit of a completed session
  returns the stored result, so a retried request is safe.
"""

import secrets
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fordona_connect.core.errors import AppError
from fordona_connect.core.logging import get_logger
from fordona_connect.learning.content.models import ContentQuestionRevision
from fordona_connect.learning.content.snapshot import SnapshotProvider
from fordona_connect.learning.entitlement import VerifiedSubscriptionSource
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
from fordona_connect.learning.mock_exam_sessions import scoring, store
from fordona_connect.learning.mock_exam_sessions.models import MockExamItem, MockExamSession
from fordona_connect.learning.mock_exam_sessions.scoring import MockExamRules
from fordona_connect.learning.progress import store as progress_store
from fordona_connect.learning.progress.screens import title_in
from fordona_connect.learning.theory_models import (
    TheoryAccess,
    TheoryAttemptItemRef,
    TheoryMockExamAccess,
    TheoryMockExamGradedItem,
    TheoryMockExamHistoryEntry,
    TheoryMockExamResult,
    TheoryMockExamResults,
    TheoryMockExamScore,
    TheoryMockExamSession,
)

log = get_logger(__name__)

FALLBACK_LOCALE = "sv-SE"
ITEM_PHASE = "EXAM"
_OPTION_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_RANDOM = secrets.SystemRandom()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _shuffle(values: Sequence[str]) -> list[str]:
    return _RANDOM.sample(list(values), len(values))


class MockExamSessionService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        snapshots: SnapshotProvider,
        subscriptions: VerifiedSubscriptionSource,
        *,
        rules: MockExamRules | None = None,
        now: Callable[[], datetime] = _utc_now,
        shuffle: Callable[[Sequence[str]], list[str]] = _shuffle,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._snapshots = snapshots
        self._subscriptions = subscriptions
        self._rules = rules or MockExamRules()
        self._now = now
        self._shuffle = shuffle

    # --- access -----------------------------------------------------------------

    async def access(self, learner_id: str) -> TheoryAccess:
        """The learner's access level and mock exam allowance, for the home screens."""
        subscribed = await self._is_subscribed(learner_id)
        async with self._sessionmaker() as session:
            available = await progress_store.any_mock_exam(session)
            used = 0 if subscribed else await store.free_attempts_used(session, learner_id)
        remaining = (
            None if subscribed else scoring.free_attempts_remaining(self._rules.free_attempts, used)
        )
        return TheoryAccess(
            level="SUBSCRIBED" if subscribed else "FREE",
            mock_exams=TheoryMockExamAccess(
                available=available,
                free_attempts_limit=self._rules.free_attempts,
                free_attempts_remaining=remaining,
            ),
        )

    # --- readiness --------------------------------------------------------------

    async def results(self, learner_id: str, *, recent: int) -> TheoryMockExamResults:
        """The learner's last ``recent`` completed sittings and their best one."""
        async with self._sessionmaker() as session:
            rows = await store.completed_sessions(session, learner_id)  # newest first
        scores = [_score(row) for row in rows]
        best = max(scores, key=lambda score: score.score_percent, default=None)
        return TheoryMockExamResults(recent=scores[: max(recent, 0)], best=best)

    # --- start ------------------------------------------------------------------

    async def start(
        self, learner_id: str, mock_exam_id: str, request_id: uuid.UUID
    ) -> TheoryMockExamSession:
        async with self._sessionmaker() as session:
            existing = await store.find_by_request(session, learner_id, request_id)
            if existing is not None:
                if existing.mock_exam_id != mock_exam_id:
                    raise RequestIdConflictError(
                        f"requestId {request_id} was already used to start a different mock exam"
                    )
                return await self._session_out(session, existing)

        await self._time_out_overdue(learner_id, mock_exam_id)

        async with self._sessionmaker() as session:
            open_session = await store.find_open(session, learner_id, mock_exam_id)
            if open_session is not None:
                return await self._session_out(session, open_session)
            exam = await store.get_exam(session, mock_exam_id)
            if exam is None:
                raise MockExamNotFoundError(f"Mock exam {mock_exam_id} not found")
            question_ids = await store.exam_question_ids(session, mock_exam_id)

        snapshot = await self._snapshots.get()
        questions = [q for qid in question_ids if (q := snapshot.question(qid)) is not None]
        if not exam.complete or len(questions) < exam.question_count:
            raise MockExamUnavailableError(
                f"Mock exam {mock_exam_id} has {len(questions)} servable question(s) "
                f"of {exam.question_count}"
            )
        by_id = {q.question_id: q for q in questions[: exam.question_count]}
        order = self._shuffle(list(by_id))

        # Verified, not failed closed: guessing "not subscribed" would spend a paying
        # learner's free attempt, which never comes back. Raises a retryable 503 instead.
        subscribed = await self._subscriptions.verified_is_subscribed(learner_id)
        session_id = uuid.uuid4()
        started_at = self._now()
        time_limit = self._rules.time_limit_seconds or None
        values: dict[str, Any] = {
            "session_id": session_id,
            "learner_id": learner_id,
            "mock_exam_id": mock_exam_id,
            "mock_exam_version": exam.current_version,
            "request_id": request_id,
            "status": "IN_PROGRESS",
            "free_attempt": not subscribed,
            "question_count": len(order),
            "time_limit_seconds": time_limit,
            "pass_percent": self._rules.pass_percent,
            "started_at": started_at,
            "expires_at": started_at + timedelta(seconds=time_limit) if time_limit else None,
        }
        items = [
            {
                "session_id": session_id,
                "seq": seq,
                "learner_id": learner_id,
                "question_id": question_id,
                "version": by_id[question_id].version,
                "option_order": self._shuffle(by_id[question_id].option_keys),
            }
            for seq, question_id in enumerate(order, start=1)
        ]

        remaining: int | None = None
        try:
            async with self._sessionmaker() as session, session.begin():
                if not subscribed:
                    await store.lock_learner(session, learner_id)
                    used = await store.free_attempts_used(session, learner_id)
                    if used >= self._rules.free_attempts:
                        raise MockExamAttemptsExhaustedError()
                    remaining = scoring.free_attempts_remaining(self._rules.free_attempts, used + 1)
                await store.insert_session(session, values, items)
        except IntegrityError:
            # A concurrent start won one of the unique indexes: serve its session.
            async with self._sessionmaker() as session:
                winner = await store.find_by_request(
                    session, learner_id, request_id
                ) or await store.find_open(session, learner_id, mock_exam_id)
                if winner is None:
                    raise
                return await self._session_out(session, winner)

        log.info(
            "mock_exam_started",
            session_id=str(session_id),
            mock_exam_id=mock_exam_id,
            questions=len(items),
            free_attempt=not subscribed,
        )
        return TheoryMockExamSession(
            session_id=str(session_id),
            mock_exam_id=mock_exam_id,
            status="IN_PROGRESS",
            started_at=started_at,
            expires_at=values["expires_at"],
            time_limit_seconds=time_limit,
            pass_percent=self._rules.pass_percent,
            items=[_item_ref_from_row(row) for row in items],
            free_attempts_remaining=remaining,
        )

    # --- read -------------------------------------------------------------------

    async def get(self, learner_id: str, session_id: uuid.UUID) -> TheoryMockExamSession:
        async with self._sessionmaker() as session:
            row = await self._owned(session, learner_id, session_id)
            return await self._session_out(session, row)

    async def result(
        self, learner_id: str, session_id: uuid.UUID, *, locale: str
    ) -> TheoryMockExamResult:
        async with self._sessionmaker() as session:
            row = await self._owned(session, learner_id, session_id)
            if row.status == "TIMED_OUT":
                raise MockExamTimedOutError()
            if row.status != "COMPLETED":
                raise MockExamNotSubmittedError(f"Mock exam session {session_id} is not submitted")
            items = await store.items_of(session, session_id)
            revisions = await store.revisions_for(session, items)
            return _result_out(row, items, revisions, locale)

    async def history(self, learner_id: str, *, locale: str) -> list[TheoryMockExamHistoryEntry]:
        async with self._sessionmaker() as session:
            rows = await store.completed_sessions(session, learner_id)
            names = await store.exam_names(session, sorted({row.mock_exam_id for row in rows}))
        return [
            TheoryMockExamHistoryEntry(
                session_id=str(row.session_id),
                mock_exam_id=row.mock_exam_id,
                name=title_in(names.get(row.mock_exam_id, {}), locale),
                correct_count=row.correct_count or 0,
                total_count=row.question_count,
                score_percent=row.score_percent or 0,
                passed=bool(row.passed),
                time_taken_seconds=row.time_taken_seconds or 0,
                completed_at=_completed_at(row),
            )
            for row in rows
        ]

    # --- submit -----------------------------------------------------------------

    async def submit(
        self,
        learner_id: str,
        session_id: uuid.UUID,
        answers: Mapping[int, str],
        *,
        locale: str,
    ) -> TheoryMockExamResult:
        now = self._now()
        async with self._sessionmaker() as session, session.begin():
            row = await store.get_session(session, session_id, for_update=True)
            if row is None or row.learner_id != learner_id:
                raise MockExamSessionNotFoundError(f"Mock exam session not found: {session_id}")
            items = await store.items_of(session, session_id)
            revisions = await store.revisions_for(session, items)
            if row.status == "COMPLETED":
                # A retried submit: the stored result, never a regrade.
                return _result_out(row, items, revisions, locale)
            if row.status == "IN_PROGRESS" and not self._overdue(row, now):
                return self._grade(row, items, revisions, answers, now, locale)

        # Past the deadline (or already timed out): close it in its own transaction so
        # the error below does not roll the status change back.
        async with self._sessionmaker() as session, session.begin():
            await store.update_session(session, session_id, status="TIMED_OUT")
        log.info("mock_exam_timed_out", session_id=str(session_id))
        raise MockExamTimedOutError()

    def _grade(
        self,
        row: MockExamSession,
        items: Sequence[MockExamItem],
        revisions: Mapping[tuple[str, str], ContentQuestionRevision],
        answers: Mapping[int, str],
        now: datetime,
        locale: str,
    ) -> TheoryMockExamResult:
        seqs = {item.seq for item in items}
        unknown = sorted(set(answers) - seqs)
        if unknown:
            raise ItemNotFoundError(f"No item at seq={unknown[0]} for session {row.session_id}")
        correct = 0
        for item in items:
            revision = _revision(revisions, item)
            selected = answers.get(item.seq)
            if selected is not None and selected not in revision.option_keys:
                raise OptionInvalidError(f"Not a valid option for seq={item.seq}: {selected}")
            item.selected_option_key = selected
            item.is_correct = selected is not None and selected == revision.correct_option_key
            correct += int(item.is_correct)

        score = scoring.score_percent(correct, len(items))
        row.status = "COMPLETED"
        row.correct_count = correct
        row.score_percent = score
        row.passed = scoring.passed(score, row.pass_percent)
        row.time_taken_seconds = scoring.time_taken_seconds(row.started_at, now, row.expires_at)
        row.completed_at = now
        log.info(
            "mock_exam_submitted",
            session_id=str(row.session_id),
            answered=len(answers),
            score_percent=score,
            passed=row.passed,
        )
        return _result_out(row, items, revisions, locale)

    # --- helpers ----------------------------------------------------------------

    async def _is_subscribed(self, learner_id: str) -> bool:
        try:
            return await self._subscriptions.is_subscribed(learner_id)
        except AppError as exc:
            log.warning("mock_exam_entitlement_check_failed_closed", error=repr(exc))
            return False

    def _overdue(self, row: MockExamSession, now: datetime) -> bool:
        if row.expires_at is None:
            return False
        return now > row.expires_at + timedelta(seconds=self._rules.submit_grace_seconds)

    async def _time_out_overdue(self, learner_id: str, mock_exam_id: str) -> None:
        """Close an open session whose deadline passed, so a new start is a new sitting."""
        now = self._now()
        async with self._sessionmaker() as session, session.begin():
            open_session = await store.find_open(session, learner_id, mock_exam_id)
            if open_session is not None and self._overdue(open_session, now):
                await store.update_session(session, open_session.session_id, status="TIMED_OUT")
                log.info("mock_exam_timed_out", session_id=str(open_session.session_id))

    async def _owned(
        self, session: AsyncSession, learner_id: str, session_id: uuid.UUID
    ) -> MockExamSession:
        row = await store.get_session(session, session_id)
        if row is None or row.learner_id != learner_id:
            raise MockExamSessionNotFoundError(f"Mock exam session not found: {session_id}")
        return row

    async def _session_out(
        self, session: AsyncSession, row: MockExamSession
    ) -> TheoryMockExamSession:
        items = await store.items_of(session, row.session_id)
        subscribed = await self._is_subscribed(row.learner_id)
        remaining = None
        if not subscribed:
            used = await store.free_attempts_used(session, row.learner_id)
            remaining = scoring.free_attempts_remaining(self._rules.free_attempts, used)
        return TheoryMockExamSession(
            session_id=str(row.session_id),
            mock_exam_id=row.mock_exam_id,
            status=row.status,
            started_at=row.started_at,
            expires_at=row.expires_at,
            time_limit_seconds=row.time_limit_seconds,
            pass_percent=row.pass_percent,
            items=[_item_ref(item) for item in items],
            free_attempts_remaining=remaining,
        )


def _score(row: MockExamSession) -> TheoryMockExamScore:
    return TheoryMockExamScore(
        score_percent=row.score_percent or 0,
        correct_count=row.correct_count or 0,
        total_count=row.question_count,
        passed=bool(row.passed),
    )


def _revision(
    revisions: Mapping[tuple[str, str], ContentQuestionRevision], item: MockExamItem
) -> ContentQuestionRevision:
    revision = revisions.get((item.question_id, item.version))
    if revision is None:
        # The foreign key guarantees the row; reaching this is a bug, not learner input.
        raise RuntimeError(f"Pinned revision missing: {item.question_id}:{item.version}")
    return revision


def _completed_at(row: MockExamSession) -> datetime:
    if row.completed_at is None:
        raise RuntimeError(f"Completed session {row.session_id} has no completed_at")
    return row.completed_at


def _result_out(
    row: MockExamSession,
    items: Sequence[MockExamItem],
    revisions: Mapping[tuple[str, str], ContentQuestionRevision],
    locale: str,
) -> TheoryMockExamResult:
    graded: list[TheoryMockExamGradedItem] = []
    for item in items:
        revision = _revision(revisions, item)
        order = list(item.option_order)
        correct_key = revision.correct_option_key
        explanation = revision.explanation or {}
        graded.append(
            TheoryMockExamGradedItem(
                seq=item.seq,
                question_id=item.question_id,
                version=item.version,
                selected_option_key=item.selected_option_key,
                correct_option_key=correct_key,
                correct_option_label=(
                    _OPTION_LABELS[order.index(correct_key)] if correct_key in order else ""
                ),
                is_correct=bool(item.is_correct),
                explanation=explanation.get(locale, explanation.get(FALLBACK_LOCALE)),
            )
        )
    return TheoryMockExamResult(
        session_id=str(row.session_id),
        mock_exam_id=row.mock_exam_id,
        status=row.status,
        correct_count=row.correct_count or 0,
        total_count=row.question_count,
        score_percent=row.score_percent or 0,
        pass_percent=row.pass_percent,
        passed=bool(row.passed),
        time_taken_seconds=row.time_taken_seconds or 0,
        completed_at=_completed_at(row),
        items=graded,
    )


def _item_ref(item: MockExamItem) -> TheoryAttemptItemRef:
    return TheoryAttemptItemRef(
        seq=item.seq,
        phase=ITEM_PHASE,
        question_id=item.question_id,
        version=item.version,
        option_order=list(item.option_order),
        answered=item.selected_option_key is not None,
    )


def _item_ref_from_row(row: Mapping[str, Any]) -> TheoryAttemptItemRef:
    return TheoryAttemptItemRef(
        seq=row["seq"],
        phase=ITEM_PHASE,
        question_id=row["question_id"],
        version=row["version"],
        option_order=list(row["option_order"]),
        answered=False,
    )
