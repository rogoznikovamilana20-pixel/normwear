import logging

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

log = logging.getLogger("db")


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.db_url, echo=False)
SessionMaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Если база создана старой версией схемы (например products.brand вместо brand_id),
        # create_all её не чинит — пересоздаём с нуля. Данных реальных клиентов там ещё нет.
        def _product_columns(sync_conn) -> set:
            insp = inspect(sync_conn)
            if "products" not in insp.get_table_names():
                return set()
            return {c["name"] for c in insp.get_columns("products")}

        cols = await conn.run_sync(_product_columns)
        if cols and "brand_id" not in cols:
            log.warning("Старая схема products%s — пересоздаю все таблицы", sorted(cols))
            if conn.dialect.name == "postgresql":
                from sqlalchemy import text

                # старые таблицы (market_snapshots, favorites, source_posts) держат FK на products
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
            else:
                await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            log.warning("Схема пересоздана")