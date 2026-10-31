"""add code_sha256 to runs

Revision ID: 20261101_0002
Revises: 20261031_0001
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_0002"
down_revision: str | None = "20261031_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("runs")]
    if "code_sha256" in columns:
        return
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "code_sha256",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("code_sha256")
