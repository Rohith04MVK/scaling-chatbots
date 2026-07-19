from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from chatlog.db.session import get_session
from chatlog.services.log_store import LogStore, PostgresLogStore

SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_log_store(session: SessionDependency) -> LogStore:
    return PostgresLogStore(session)


LogStoreDependency = Annotated[LogStore, Depends(get_log_store)]
