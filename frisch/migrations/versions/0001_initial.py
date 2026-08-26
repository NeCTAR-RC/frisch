"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-25

"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=True),
    )
    op.create_table(
        "instance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("service.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("env", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("instance", sa.String(64), nullable=True),
        sa.Column("current_version", sa.String(255), nullable=True),
        sa.Column("current_version_kind", sa.String(16), nullable=False),
        sa.Column("current_changed_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source", "env", "source_key", "component", name="uq_instance"
        ),
    )
    op.create_table(
        "version_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "instance_id",
            sa.Integer(),
            sa.ForeignKey("instance.id"),
            nullable=False,
        ),
        sa.Column("version", sa.String(255), nullable=True),
        sa.Column("previous_version", sa.String(255), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("precision", sa.String(16), nullable=False),
        sa.Column("interval_start", sa.DateTime(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("ref", sa.String(128), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.UniqueConstraint("instance_id", "ref", name="uq_event_ref"),
    )
    op.create_index(
        "ix_event_instance_changed",
        "version_event",
        ["instance_id", "changed_at"],
    )
    op.create_table(
        "collector_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("env", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
    )
    op.create_table(
        "git_cursor",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("sha", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("git_cursor")
    op.drop_table("collector_run")
    op.drop_index("ix_event_instance_changed", table_name="version_event")
    op.drop_table("version_event")
    op.drop_table("instance")
    op.drop_table("service")
