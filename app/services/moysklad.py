import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionMaker
from app.models import Brand, Category, Product, ProductPhoto, utcnow
from app.services.photos import yandex_library
from app.services.pricing import retail_price

log = logging.getLogger("moysklad")
settings = get_settings()


class MoyskladClient:
    """Клиент для работы с API МойСклад"""

    def __init__(self):
        self.base_url = "https://api.moysklad.ru/api/remap/1.2"
        self.token = settings.moysklad_token
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def get_assortment(self, limit: int = 1000, offset: int = 0) -> dict:
        """Получить ассортимент с остатками"""
        try:
            response = await self.client.get(
                f"{self.base_url}/entity/assortment",
                params={
                    "limit": limit,
                    "offset": offset,
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log.error("Ошибка получения ассортимента: %s", e)
            return {}

    async def get_all_products(self) -> list:
        """Получить все товары (пагинация)"""
        all_products = []
        offset = 0
        limit = 1000

        while True:
            data = await self.get_assortment(limit=limit, offset=offset)
            if not data or "rows" not in data:
                break

            rows = data["rows"]
            all_products.extend(rows)

            if len(rows) < limit:
                break

            offset += limit
            log.info("Загружено %d товаров из МойСклад", len(all_products))

        log.info("Всего загружено %d товаров", len(all_products))
        return all_products

    async def sync_products(self):
        """Синхронизировать товары с БД"""
        if not self.token:
            log.warning("MOYSKLAD_TOKEN не настроен, синхронизация пропущена")
            return

        log.info("Начинаем синхронизацию с МойСклад...")
        products = await self.get_all_products()

        if not products:
            log.warning("Не получены товары из МойСклад")
            return

        async with SessionMaker() as s:
            synced = 0
            updated = 0
            created = 0

            for item in products:
                try:
                    # Парсинг данных из МойСклад
                    product_name = item.get("name", "").strip()
                    if not product_name:
                        continue

                    # Цена поставщика (в копейках → рубли)
                    supplier_price = 0
                    if "salePrices" in item and item["salePrices"]:
                        supplier_price = item["salePrices"][0].get("value", 0) / 100

                    # Остаток
                    stock = item.get("stock", 0)
                    if stock < 0:
                        stock = 0

                    # Артикул
                    article = item.get("article", "").strip()

                    # Описание
                    description = item.get("description", "").strip()

                    # Код товара (ID в МойСклад)
                    external_id = item.get("id", "")

                    # Расчёт розничной цены с наценкой из настроек
                    retail = retail_price(supplier_price, settings.default_margin_pct)

                    # Поиск существующего товара: сначала по артикулу поставщика,
                    # потом по внешнему ID (UUID МойСклада). Совпадение по UUID без
                    # артикула НЕ трогаем во избежание дублей чужих позиций.
                    existing = None
                    ms_article = (item.get("article", "") or "").strip()
                    if ms_article:
                        existing = (
                            await s.execute(select(Product).where(Product.article == ms_article))
                        ).scalar_one_or_none()
                    if existing is None and external_id:
                        existing = (
                            await s.execute(select(Product).where(Product.article == external_id))
                        ).scalar_one_or_none()

                    if existing:
                        # Обновление существующего: только цены и остаток.
                        # Название/описание/статус не трогаем — их ведёт модерация.
                        existing.supplier_price = supplier_price
                        existing.retail_price = retail
                        existing.stock = int(stock)

                        updated += 1
                    else:
                        # Создание нового товара — всегда на модерацию (pending):
                        # без фото и проверки публиковать в витрину нельзя.
                        # Определение бренда из названия
                        brand = self._extract_brand(product_name)
                        brand_obj = await self._get_or_create_brand(s, brand)

                        # Определение категории
                        category = self._extract_category(product_name)
                        category_obj = await self._get_or_create_category(s, category)

                        new_product = Product(
                            title=product_name,
                            supplier_price=supplier_price,
                            retail_price=retail,
                            stock=int(stock),
                            article=external_id,
                            description=description,
                            brand_id=brand_obj.id if brand_obj else None,
                            category_id=category_obj.id if category_obj else None,
                            status="pending",
                            photo_mode="moysklad",
                        )
                        s.add(new_product)
                        created += 1

                    synced += 1

                except Exception as e:
                    log.error("Ошибка обработки товара %s: %s", item.get("name", "unknown"), e)
                    continue

            await s.commit()
            log.info(
                "Синхронизация завершена: %d товаров обработано, %d создано, %d обновлено",
                synced,
                created,
                updated,
            )

        await self.client.aclose()

    def _extract_brand(self, product_name: str) -> str:
        """Извлечь бренд из названия товара"""
        # Попытка определить бренд по первым словам
        words = product_name.split()
        if len(words) >= 2:
            potential_brand = f"{words[0]} {words[1]}"
            # Проверяем на известные бренды
            known_brands = yandex_library.brand_titles
            if potential_brand in known_brands:
                return potential_brand
        return "Unknown"

    def _extract_category(self, product_name: str) -> str:
        """Извлечь категорию из названия товара"""
        product_lower = product_name.lower()

        if any(word in product_lower for word in ["футболка", "tee", "t-shirt"]):
            return "Футболки"
        elif any(word in product_lower for word in ["худи", "hoodie", "sweatshirt"]):
            return "Худи"
        elif any(word in product_lower for word in ["лонгслив", "longsleeve"]):
            return "Лонгсливы"
        elif any(word in product_lower for word in ["штаны", "pants", "джинсы", "jeans"]):
            return "Штаны"
        elif any(word in product_lower for word in ["кроссовки", "sneakers", "bot"]):
            return "Кроссовки"
        elif any(word in product_lower for word in ["куртка", "jacket", "windbreaker"]):
            return "Куртки"
        else:
            return "Разное"

    async def _get_or_create_brand(self, session, brand_name: str):
        """Получить или создать бренд"""
        if not brand_name or brand_name == "Unknown":
            return None

        result = await session.execute(select(Brand).where(Brand.title == brand_name))
        brand = result.scalar_one_or_none()

        if not brand:
            slug = brand_name.lower().replace(" ", "-")
            brand = Brand(title=brand_name, slug=slug)
            session.add(brand)
            await session.flush()

        return brand

    async def _get_or_create_category(self, session, category_name: str):
        """Получить или создать категорию"""
        result = await session.execute(select(Category).where(Category.title == category_name))
        category = result.scalar_one_or_none()

        if not category:
            slug = category_name.lower().replace(" ", "-")
            category = Category(title=category_name, slug=slug)
            session.add(category)
            await session.flush()

        return category


async def sync_loop():
    """Цикл автоматической синхронизации каждые 6 часов"""
    client = MoyskladClient()

    while True:
        try:
            await client.sync_products()
            log.info("Следующая синхронизация через 6 часов")
        except Exception as e:
            log.error("Ошибка в цикле синхронизации: %s", e)

        # Ждём 6 часов
        import asyncio

        await asyncio.sleep(6 * 60 * 60)
