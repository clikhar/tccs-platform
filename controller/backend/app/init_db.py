import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine

from .models import Base


async def main() -> None:
    url = os.getenv("DATABASE_URL", "postgresql+asyncpg://tccs:change-me-local@localhost:5432/tccs")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
