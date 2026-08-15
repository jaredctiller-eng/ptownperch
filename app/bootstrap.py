"""Startup reconciliation of env-declared configuration into the database."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Property

logger = logging.getLogger(__name__)


async def upsert_configured_property(session: AsyncSession) -> Property | None:
    """Create or update the property declared via environment variables.

    Returns the property, or None when no iCal URL is configured (the app then
    simply has nothing to sync). Idempotent: safe to run on every boot.
    """
    if not settings.airbnb_ical_url or not settings.property_slug:
        return None

    prop = (
        await session.execute(
            select(Property).where(Property.slug == settings.property_slug)
        )
    ).scalar_one_or_none()

    if prop is None:
        prop = Property(
            slug=settings.property_slug,
            name=settings.property_name,
            airbnb_ical_url=settings.airbnb_ical_url,
        )
        session.add(prop)
        logger.info("created property %s from environment config", prop.slug)
    elif (prop.name, prop.airbnb_ical_url) != (
        settings.property_name,
        settings.airbnb_ical_url,
    ):
        prop.name = settings.property_name
        prop.airbnb_ical_url = settings.airbnb_ical_url
        logger.info("updated property %s from environment config", prop.slug)

    await session.commit()
    return prop
