from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db_models import Call


class CallRepository:
    """Persistence operations for TCCS call records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, call: Call) -> Call:
        self.session.add(call)
        await self.session.flush()
        return call

    async def get(self, call_id: UUID) -> Call | None:
        # Calls can remain present in the AsyncSession identity map with
        # expire_on_commit=False. Force this lookup to refresh an existing
        # instance so callers observe state committed by an earlier event.
        result = await self.session.execute(
            select(Call)
            .execution_options(populate_existing=True)
            .where(Call.id == call_id)
        )
        return result.scalar_one_or_none()
