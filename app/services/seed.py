from sqlalchemy import select

from app.models import Brand, Category
from app.services.photos import yandex_library

CATEGORIES = [
    "Кроссовки",
    "Одежда",
    "Верхняя одежда",
    "Футболки",
    "Штаны",
    "Головные уборы",
    "Сумки",
    "Аксессуары",
    "Другое",
]


def _slug(title: str) -> str:
    return title.lower().replace(" ", "_").replace("/", "_")


async def seed_basic(session) -> None:
    existing_cats = {c.title for c in (await session.scalars(select(Category))).all()}
    for title in CATEGORIES:
        if title not in existing_cats:
            session.add(Category(slug=_slug(title), title=title))
    existing_brands = {b.slug for b in (await session.scalars(select(Brand))).all()}
    for entry in yandex_library.by_brand.values():
        slug = entry["title"].lower()
        if slug not in existing_brands:
            session.add(Brand(slug=slug, title=entry["title"]))
    await session.commit()