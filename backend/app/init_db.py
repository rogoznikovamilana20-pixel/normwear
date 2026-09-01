import asyncio
from .db import engine
from .models import Base

async def main():
    # Prefer alembic migrations if available, fallback to create_all for dev
    try:
        from alembic.config import Config
        from alembic import command
        import pathlib
        ini = pathlib.Path(__file__).resolve().parents[1] / "alembic.ini"
        if ini.exists():
            cfg = Config(str(ini))
            # alembic handles async via env.py
            command.upgrade(cfg, "head")
            print("migrated via alembic")
            return
    except Exception as e:
        print(f"alembic not used ({e}), fallback to create_all")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == '__main__':
    asyncio.run(main())
