"""Seed a property.

Usage: python -m scripts.seed_property <slug> <name> <airbnb_ical_url>
Get the iCal URL from Airbnb: Calendar -> Availability -> Connect another calendar
-> "Export calendar" link for the listing.
"""
import asyncio
import sys

from app.db import SessionLocal, engine
from app.models import Base, Property


async def main(slug: str, name: str, ical_url: str) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        session.add(Property(slug=slug, name=name, airbnb_ical_url=ical_url))
        await session.commit()
    print(f"seeded property '{slug}'")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
