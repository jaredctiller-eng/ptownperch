import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Reservation
from app.slack.notify import post_slack

logger = logging.getLogger(__name__)


def _as_aware(dt: datetime) -> datetime:
    # SQLite (used in tests / local dev) doesn't persist tzinfo even on a
    # DateTime(timezone=True) column, so a value read back from the DB comes
    # out naive even though it was written as UTC. Postgres/asyncpg (prod)
    # round-trips tz-aware values correctly, so this is a no-op there.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def expire_stale_holds(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    warn_cutoff = now - timedelta(hours=settings.hold_warning_hours)
    expire_cutoff = now - timedelta(hours=settings.hold_expiry_hours)

    holds = (
        (await session.execute(select(Reservation).where(Reservation.status == "hold")))
        .scalars()
        .all()
    )

    for res in holds:
        created_at = _as_aware(res.created_at)
        if created_at <= expire_cutoff:
            res.status = "canceled"
            await session.commit()
            try:
                await post_slack(
                    f":alarm_clock: Hold expired and released: {res.guest_name} "
                    f"{res.check_in} → {res.check_out} (#{res.id})"
                )
            except Exception:
                logger.exception("slack post failed for expired hold #%s", res.id)
        elif created_at <= warn_cutoff and res.hold_warned_at is None:
            res.hold_warned_at = now
            await session.commit()
            try:
                await post_slack(
                    f":hourglass_flowing_sand: Hold will expire soon unless paid: "
                    f"{res.guest_name} {res.check_in} → {res.check_out} (#{res.id})"
                )
            except Exception:
                logger.exception("slack post failed for hold warning #%s", res.id)


async def expire_stale_holds_all_properties() -> None:
    async with SessionLocal() as session:
        try:
            await expire_stale_holds(session)
        except Exception:
            logger.exception("hold expiry sweep failed")
