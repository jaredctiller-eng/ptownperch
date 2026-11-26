"""agreed total on direct reservations

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26

Direct bookings are negotiated to one flat, tax-inclusive total over text.
That number is recorded here when the inquiry is approved in Slack, and the
contract's whole payment schedule derives from it.

Nullable with no backfill: Airbnb-sourced reservations never have one, and
direct reservations created before this migration were quoted the old way.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'reservations',
        sa.Column('agreed_total_cents', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reservations', 'agreed_total_cents')
