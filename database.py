from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, connect_args={"timeout": 30, "check_same_thread": False}, echo=False)

from sqlalchemy import event
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# 后台任务用
try:
    async_session = AsyncSessionLocal
except NameError:
    try:
        async_session = async_session_maker
    except NameError:
        async_session = None

