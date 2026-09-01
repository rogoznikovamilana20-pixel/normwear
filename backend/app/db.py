from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from .config import settings

def _url() -> str:
    u = settings.database_url
    # Render gives postgres:// or postgresql:// (psycopg2), we need asyncpg
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql+asyncpg://", 1)
    elif u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+asyncpg://", 1)
    # already asyncpg -> keep
    return u

engine = create_async_engine(_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
