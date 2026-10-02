"""add stream_metrics, stream_failures, stream_events (silver layer)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stream_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stream_key", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vehicle_id", sa.String(length=100), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_ttc", sa.Float(), nullable=True),
        sa.Column("avg_speed", sa.Float(), nullable=False, server_default="0"),
        sa.Column("speed_violations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lane_deviations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ttc_per_step", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_offsets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_key", name="uq_stream_metrics_key"),
    )
    op.create_index("ix_stream_metrics_source", "stream_metrics", ["source"])
    op.create_index("ix_stream_metrics_run", "stream_metrics", ["run_id"])
    op.create_index("ix_stream_metrics_vehicle", "stream_metrics", ["vehicle_id"])

    op.create_table(
        "stream_failures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stream_key", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vehicle_id", sa.String(length=100), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("rule", sa.String(length=100), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_key", "rule", name="uq_stream_failures_key_rule"),
    )
    op.create_index("ix_stream_failures_run", "stream_failures", ["run_id"])
    op.create_index("ix_stream_failures_severity", "stream_failures", ["severity"])

    op.create_table(
        "stream_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("vehicle_id", sa.String(length=100), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kafka_partition", sa.Integer(), nullable=False),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stream_events_event_id"),
    )
    op.create_index("ix_stream_events_vehicle", "stream_events", ["vehicle_id"])
    op.create_index("ix_stream_events_run", "stream_events", ["run_id"])
    op.create_index("ix_stream_events_source_time", "stream_events", ["source", "captured_at"])
    op.create_index("ix_stream_events_topic_offset", "stream_events", ["topic", "kafka_offset"])


def downgrade() -> None:
    op.drop_table("stream_events")
    op.drop_table("stream_failures")
    op.drop_table("stream_metrics")
