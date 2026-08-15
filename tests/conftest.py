import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Property


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def prop(session):
    p = Property(slug="perch", name="Ptown Perch")
    session.add(p)
    await session.flush()
    return p
