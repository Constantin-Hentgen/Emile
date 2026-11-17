"""shop on lists, learned aisle order

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shopping_lists", sa.Column("shop", sa.String(length=60), nullable=True))
    op.create_table(
        "aisle_ranks",
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("shop", sa.String(length=60), nullable=False),
        sa.Column("aisle", sa.String(length=60), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("seen", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"], ["households.id"], name=op.f("fk_aisle_ranks_household_id_households"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("household_id", "shop", "aisle", name=op.f("pk_aisle_ranks")),
    )


def downgrade() -> None:
    op.drop_table("aisle_ranks")
    op.drop_column("shopping_lists", "shop")
