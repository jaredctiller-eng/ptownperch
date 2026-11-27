"""flag Airbnb "Not available" blocks apart from real bookings; manual Airbnb payout

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26

The iCal sync stored every Airbnb event as a reservation, so host/system
blocks showed up as bookings. Nullable, no backfill: the next sync sets it
on every existing row from the feed's SUMMARY.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reservations', sa.Column('is_airbnb_block', sa.Boolean(), nullable=True))
    op.add_column('reservations', sa.Column('airbnb_payout_cents', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('reservations', 'airbnb_payout_cents')
    op.drop_column('reservations', 'is_airbnb_block')
