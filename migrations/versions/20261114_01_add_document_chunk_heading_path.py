from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261114_01"
down_revision: Union[str, None] = "20261113_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("heading_path", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "heading_path")
