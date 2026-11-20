"""mock exam sessions: mock_exam_session, mock_exam_item

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB()
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "mock_exam_session",
        sa.Column("session_id", _UUID, primary_key=True),
        sa.Column("learner_id", sa.Text(), nullable=False),
        sa.Column("mock_exam_id", sa.Text(), nullable=False),
        sa.Column("mock_exam_version", sa.Text()),
        sa.Column("request_id", _UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("free_attempt", sa.Boolean(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("time_limit_seconds", sa.Integer()),
        sa.Column("pass_percent", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer()),
        sa.Column("score_percent", sa.Integer()),
        sa.Column("passed", sa.Boolean()),
        sa.Column("time_taken_seconds", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "learner_id", "request_id", name="uq_mock_exam_session_learner_request"
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'TIMED_OUT')",
            name="ck_mock_exam_session_status",
        ),
        sa.CheckConstraint("question_count > 0", name="ck_mock_exam_session_question_count"),
        sa.CheckConstraint(
            "pass_percent BETWEEN 0 AND 100", name="ck_mock_exam_session_pass_percent"
        ),
    )
    # At most one open session per learner and exam: a concurrent second start
    # loses on this index and resumes the winner's session.
    op.create_index(
        "uq_mock_exam_session_one_open_per_exam",
        "mock_exam_session",
        ["learner_id", "mock_exam_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.create_index(
        "ix_mock_exam_session_learner_completed",
        "mock_exam_session",
        ["learner_id", "completed_at"],
    )

    op.create_table(
        "mock_exam_item",
        sa.Column(
            "session_id",
            _UUID,
            sa.ForeignKey("mock_exam_session.session_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("learner_id", sa.Text(), nullable=False),
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("option_order", _JSON, nullable=False),
        sa.Column("selected_option_key", sa.Text()),
        sa.Column("is_correct", sa.Boolean()),
        sa.ForeignKeyConstraint(
            ["question_id", "version"],
            ["content_question_revision.question_id", "content_question_revision.version"],
            name="fk_mock_exam_item_revision",
        ),
    )
    op.create_index("ix_mock_exam_item_learner", "mock_exam_item", ["learner_id"])


def downgrade() -> None:
    op.drop_table("mock_exam_item")
    op.drop_table("mock_exam_session")
