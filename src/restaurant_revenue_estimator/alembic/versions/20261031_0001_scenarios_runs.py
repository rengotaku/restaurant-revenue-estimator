"""scenarios and runs

Revision ID: 20261031_0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261031_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_assumption_set", sa.String(length=20), nullable=False),
        sa.Column("overrides_json", sa.String(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenarios_store_id", "scenarios", ["store_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("assumption_set", sa.String(length=20), nullable=False),
        sa.Column("calendar_mode", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("replications", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=20), nullable=False),
        sa.Column("calendar_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.String(), nullable=False),
        sa.Column("report_md", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_store_id", "runs", ["store_id"])
    op.create_index("ix_runs_input_hash", "runs", ["input_hash"])


def downgrade() -> None:
    op.drop_index("ix_runs_input_hash", table_name="runs")
    op.drop_index("ix_runs_store_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_scenarios_store_id", table_name="scenarios")
    op.drop_table("scenarios")
