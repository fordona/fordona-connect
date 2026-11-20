"""Mock exam sessions: one learner taking one Boost mock exam, and its pinned items."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from fordona_connect.db.models import Base


class MockExamSession(Base):
    """One sitting of a mock exam.

    At most one ``IN_PROGRESS`` session per learner and exam: a second start
    resumes it. ``request_id`` makes a start idempotent. ``free_attempt`` is true
    when the start used one of a free learner's attempts; counting those rows is
    the free-attempt counter, so there is no separate counter to drift.
    """

    __tablename__ = "mock_exam_session"

    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    learner_id: Mapped[str] = mapped_column(Text, nullable=False)
    # No foreign key: Boost may retire the exam while the session is history.
    mock_exam_id: Mapped[str] = mapped_column(Text, nullable=False)
    mock_exam_version: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    free_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # The rules resolved at start: a later settings change never changes a session.
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer)
    pass_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    score_percent: Mapped[int | None] = mapped_column(Integer)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("learner_id", "request_id", name="uq_mock_exam_session_learner_request"),
        Index(
            "uq_mock_exam_session_one_open_per_exam",
            "learner_id",
            "mock_exam_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
        Index("ix_mock_exam_session_learner_completed", "learner_id", "completed_at"),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'TIMED_OUT')",
            name="ck_mock_exam_session_status",
        ),
        CheckConstraint("question_count > 0", name="ck_mock_exam_session_question_count"),
        CheckConstraint("pass_percent BETWEEN 0 AND 100", name="ck_mock_exam_session_pass_percent"),
    )


class MockExamItem(Base):
    """One question of a session, pinned to the revision it is graded against."""

    __tablename__ = "mock_exam_item"

    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mock_exam_session.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    learner_id: Mapped[str] = mapped_column(Text, nullable=False)
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    # Option keys in the order shown to the learner (shuffled with a CSPRNG).
    option_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    selected_option_key: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "version"],
            ["content_question_revision.question_id", "content_question_revision.version"],
            name="fk_mock_exam_item_revision",
        ),
        Index("ix_mock_exam_item_learner", "learner_id"),
    )
