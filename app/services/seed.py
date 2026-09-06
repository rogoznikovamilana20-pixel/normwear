import json
import re

from sqlalchemy import func, select

from app.config import BASE_DIR
from app.models import Brand, Category, Product, ProductPhoto
from app.services.photos import yandex_library
from app.services.pricing import retail_price
from app.config import get_settings

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
    # автозаливка 19 товаров если каталог пуст (для Render)
    prod_cnt = (await session.execute(select(func.count()).select_from(Product))).scalar() or 0
    if prod_cnt == 0:
        try:
            settings = get_settings()
            data_path = BASE_DIR / "data" / "catalogs" / "catalog_optobaza.json"
            if data_path.exists():
                data = json.loads(data_path.read_text(encoding="utf-8"))
                # берем 19 из середины по цене как в ручном импорте
                filtered = [x for x in data if x.get("price", 0) > 0]
                filtered = sorted(filtered, key=lambda x: x["price"])
                start = max(0, len(filtered) // 3)
                chosen = filtered[start:start+19]
                # кэш брендов/категорий
                brands = {b.title.lower(): b for b in (await session.scalars(select(Brand))).all()}
                cats = {c.title: c for c in (await session.scalars(select(Category))).all()}
                cat_map = {"Longsleeve": "Одежда", "Tee": "Футболки", "Hoodie": "Одежда", "Pants": "Штаны", "Shorts": "Штаны", "Jacket": "Верхняя одежда", "Shoes": "Кроссовки", "Sneakers": "Кроссовки", "Bag": "Сумки", "Cap": "Головные уборы"}

                def _parse_sizes(s):
                    if not s or s in ("—", "-", ""):
                        return []
                    return [p.strip() for p in re.split(r"[,\s]+", s) if p.strip() and p.strip() not in ("—", "-")]

                for item in chosen:
                    title = (item.get("title") or "").strip() or f"{item.get('brand')} {item.get('category')}"
                    brand_name = (item.get("brand") or "").strip()
                    if not brand_name or brand_name.lower() in ("other", "другое", ""):
                        # детектим из title если бренд не указан
                        from app.services.parser import detect_brand

                        detected = detect_brand(title)
                        brand_name = detected or title.split()[0] if title else "Other"
                    brand = brands.get(brand_name.lower())
                    if not brand:
                        slug = brand_name.lower().replace(" ", "_").replace("/", "_")[:64]
                        brand = (await session.scalars(select(Brand).where(Brand.slug == slug))).first()
                        if not brand:
                            brand = Brand(slug=slug, title=brand_name[:64])
                            session.add(brand)
                            await session.flush()
                            brands[brand_name.lower()] = brand
                    cat_title = cat_map.get(item.get("category", "Другое"), "Другое")
                    cat = cats.get(cat_title)
                    cat_id = cat.id if cat else None
                    sup_price = float(item.get("price", 0))
                    retail = retail_price(sup_price, settings.default_margin_pct)
                    sizes = _parse_sizes(item.get("sizes", ""))
                    if not sizes:
                        m = re.search(r"Размерный ряд[:\s]*([SMLXL0-9,\s]+)", item.get("description", "") or "")
                        if m:
                            sizes = _parse_sizes(m.group(1))
                    if not sizes:
                        sizes = ["M", "L"]
                    prod = Product(
                        brand_id=brand.id,
                        category_id=cat_id,
                        title=title[:255],
                        description=(item.get("description") or title)[:2000],
                        article=f"OPTO{item.get('msg_id','')}",
                        supplier_price=sup_price,
                        retail_price=retail,
                        sizes=sizes[:8],
                        stock=int(item.get("stock", 1) or 1),
                        status="published",
                        photo_mode="yandex",
                        supplier_text=f"{title} {sup_price}",
                    )
                    session.add(prod)
                    await session.flush()
                    paths = yandex_library.paths_for(brand_name, limit=4)
                    if not paths:
                        for bt in list(brands.keys())[:5]:
                            paths = yandex_library.paths_for(bt, limit=2)
                            if paths:
                                break
                    for i, p in enumerate(paths[:4]):
                        session.add(ProductPhoto(product_id=prod.id, source="yandex", url=p, position=i))
                    if not paths:
                        session.add(ProductPhoto(product_id=prod.id, source="supplier", url="https://via.placeholder.com/600", position=0))
                await session.commit()
        except Exception:
            await session.rollback()
            pass