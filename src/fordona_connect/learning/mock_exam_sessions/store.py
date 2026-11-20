"""Mock exam session queries. Every function takes the caller's session."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from fordona_connect.learning.content.models import (
    ContentMockExam,
    ContentMockExamQuestion,
    ContentQuestionRevision,
)
from fordona_connect.learning.mock_exam_sessions.models import MockExamItem, MockExamSession


async def find_by_request(
    session: AsyncSession, learner_id: str, request_id: UUID
) -> MockExamSession | None:
    return await session.scalar(
        select(MockExamSession).where(
            MockExamSession.learner_id == learner_id,
            MockExamSession.request_id == request_id,
        )
    )


async def find_open(
    session: AsyncSession, learner_id: str, mock_exam_id: str
) -> MockExamSession | None:
    return await session.scalar(
        select(MockExamSession).where(
            MockExamSession.learner_id == learner_id,
            MockExamSession.mock_exam_id == mock_exam_id,
            MockExamSession.status == "IN_PROGRESS",
        )
    )


async def get_session(
    session: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> MockExamSession | None:
    query = select(MockExamSession).where(MockExamSession.session_id == session_id)
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


async def lock_learner(session: AsyncSession, learner_id: str) -> None:
    """Serialise free-attempt checks per learner for the rest of the transaction."""
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext("mock_exam:" + learner_id)))
    )


async def free_attempts_used(session: AsyncSession, learner_id: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(MockExamSession)
        .where(MockExamSession.learner_id == learner_id, MockExamSession.free_attempt.is_(True))
    )
    return int(count or 0)


async def get_exam(session: AsyncSession, mock_exam_id: str) -> ContentMockExam | None:
    return await session.get(ContentMockExam, mock_exam_id)


async def exam_question_ids(session: AsyncSession, mock_exam_id: str) -> list[str]:
    rows = await session.scalars(
        select(ContentMockExamQuestion.question_id)
        .where(ContentMockExamQuestion.mock_exam_id == mock_exam_id)
        .order_by(ContentMockExamQuestion.sort)
    )
    return list(rows)


async def exam_names(
    session: AsyncSession, mock_exam_ids: Sequence[str]
) -> dict[str, dict[str, str]]:
    if not mock_exam_ids:
        return {}
    rows = await session.execute(
        select(ContentMockExam.mock_exam_id, ContentMockExam.name).where(
            ContentMockExam.mock_exam_id.in_(mock_exam_ids)
        )
    )
    return {exam_id: name for exam_id, name in rows}


async def insert_session(
    session: AsyncSession, values: dict[str, Any], items: Sequence[dict[str, Any]]
) -> None:
    session.add(MockExamSession(**values))
    await session.flush()
    session.add_all(MockExamItem(**item) for item in items)
    await session.flush()


async def items_of(session: AsyncSession, session_id: UUID) -> list[MockExamItem]:
    rows = await session.scalars(
        select(MockExamItem).where(MockExamItem.session_id == session_id).order_by(MockExamItem.seq)
    )
    return list(rows)


async def revisions_for(
    session: AsyncSession, items: Sequence[MockExamItem]
) -> dict[tuple[str, str], ContentQuestionRevision]:
    if not items:
        return {}
    keys = sorted({(item.question_id, item.version) for item in items})
    rows = await session.scalars(
        select(ContentQuestionRevision).where(
            tuple_(ContentQuestionRevision.question_id, ContentQuestionRevision.version).in_(keys)
        )
    )
    return {(rev.question_id, rev.version): rev for rev in rows}


async def update_session(session: AsyncSession, session_id: UUID, **values: Any) -> None:
    await session.execute(
        update(MockExamSession)
        .where(MockExamSession.session_id == session_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def completed_sessions(session: AsyncSession, learner_id: str) -> list[MockExamSession]:
    rows = await session.scalars(
        select(MockExamSession)
        .where(MockExamSession.learner_id == learner_id, MockExamSession.status == "COMPLETED")
        .order_by(MockExamSession.completed_at.desc())
    )
    return list(rows)
