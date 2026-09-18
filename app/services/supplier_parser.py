"""
Парсер товаров с публичной страницы МойСклад поставщика
Использует Playwright для обхода JavaScript
"""

import asyncio
import logging
from typing import List, Dict

from app.config import get_settings
from app.db import SessionMaker
from app.models import Product, Brand, Category, utcnow
from app.services.pricing import retail_price

log = logging.getLogger("supplier_parser")
settings = get_settings()


class SupplierParser:
    """Парсер товаров с публичной страницы МойСклад"""

    def __init__(self, public_url: str):
        self.public_url = public_url
        self.products = []

    async def parse_with_playwright(self) -> List[Dict]:
        """Парсинг страницы через Playwright"""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                log.info("Загрузка страницы поставщика: %s", self.public_url)
                await page.goto(self.public_url, wait_until="domcontentloaded", timeout=60000)

                # Ждём загрузки таблицы с товарами
                await page.wait_for_selector("table, .table, .grid, [data-testid*='table']", timeout=15000)

                # Извлекаем данные из таблицы
                products = await self._extract_table_data(page)

                await browser.close()
                return products

        except ImportError:
            log.error("Playwright не установлен. Установи: pip install playwright")
            return []
        except Exception as e:
            log.error("Ошибка парсинга через Playwright: %s", e)
            return []

    async def _extract_table_data(self, page) -> List[Dict]:
        """Извлечение данных из таблицы"""
        products = []

        try:
            # Пробуем разные селекторы для таблиц МойСклад
            selectors = [
                "table tbody tr",
                ".table tbody tr",
                "[role='row']",
                ".data-table tr",
            ]

            rows = []
            for selector in selectors:
                try:
                    rows = await page.query_selector_all(selector)
                    if rows:
                        log.info("Найдено %d строк через селектор: %s", len(rows), selector)
                        break
                except:
                    continue

            if not rows:
                log.warning("Не найдены строки таблицы")
                return []

            # Парсим каждую строку
            for row in rows[1:]:  # Пропускаем заголовок
                try:
                    cells = await row.query_selector_all("td, [role='gridcell']")
                    if len(cells) >= 3:  # Минимум: название, цена, остаток
                        product_data = await self._parse_row(cells)
                        if product_data:
                            products.append(product_data)
                except Exception as e:
                    log.error("Ошибка парсинга строки: %s", e)
                    continue

            log.info("Успешно распарсено %d товаров", len(products))
            return products

        except Exception as e:
            log.error("Ошибка извлечения данных таблицы: %s", e)
            return []

    async def _parse_row(self, cells) -> Dict | None:
        """Парсинг одной строки таблицы"""
        try:
            # Получаем текст из ячеек
            texts = []
            for cell in cells:
                text = await cell.inner_text()
                texts.append(text.strip())

            # Ожидаемый формат МойСклад: название, артикул, цена, остаток
            if len(texts) < 2:
                return None

            name = texts[0]
            supplier_price = self._extract_price(texts)
            stock = self._extract_stock(texts)
            article = self._extract_article(texts)

            if not name or supplier_price <= 0:
                return None

            return {
                "name": name,
                "supplier_price": supplier_price,
                "stock": stock,
                "article": article,
            }

        except Exception as e:
            log.error("Ошибка парсинга ячеек: %s", e)
            return None

    def _extract_price(self, texts: list) -> float:
        """Извлечение цены из текста"""
        for text in texts:
            # Ищем число с возможными разделителями
            import re

            prices = re.findall(r"[\d\s.,]+", text)
            for price_str in prices:
                try:
                    # Убираем пробелы и заменяем запятую на точку
                    cleaned = price_str.replace(" ", "").replace(",", ".")
                    price = float(cleaned)
                    if price > 0:
                        return price
                except:
                    continue
        return 0.0

    def _extract_stock(self, texts: list) -> int:
        """Извлечение остатка из текста"""
        for text in texts:
            import re

            # Ищем целые числа (остатки)
            stocks = re.findall(r"\d+", text)
            for stock_str in stocks:
                try:
                    stock = int(stock_str)
                    if stock >= 0:
                        return stock
                except:
                    continue
        return 0

    def _extract_article(self, texts: list) -> str:
        """Извлечение артикула из текста"""
        # Обычно артикул - это короткий alphanumeric код
        import re

        for text in texts:
            # Ищем паттерн артикула (буквы+цифры, 3-20 символов)
            articles = re.findall(r"[A-Za-z0-9]{3,20}", text)
            if articles:
                return articles[0]
        return ""

    async def sync_to_database(self):
        """Синхронизация распарсенных товаров с БД"""
        if not self.products:
            log.warning("Нет товаров для синхронизации")
            return

        async with SessionMaker() as s:
            synced = 0
            created = 0
            updated = 0

            for product_data in self.products:
                try:
                    name = product_data["name"]
                    supplier_price = product_data["supplier_price"]
                    stock = product_data["stock"]
                    article = product_data["article"]

                    # Расчёт розничной цены
                    retail = retail_price(supplier_price)

                    # Поиск существующего товара
                    existing = None
                    if article:
                        result = await s.execute(select(Product).where(Product.article == article))
                        existing = result.scalar_one_or_none()

                    if existing:
                        # Обновление
                        existing.title = name
                        existing.supplier_price = supplier_price
                        existing.retail_price = retail
                        existing.stock = stock
                        existing.status = "published" if stock > 0 else "pending"
                        existing.updated_at = utcnow()
                        updated += 1
                    else:
                        # Создание
                        brand = self._extract_brand(name)
                        brand_obj = await self._get_or_create_brand(s, brand)

                        category = self._extract_category(name)
                        category_obj = await self._get_or_create_category(s, category)

                        new_product = Product(
                            title=name,
                            supplier_price=supplier_price,
                            retail_price=retail,
                            stock=stock,
                            article=article,
                            brand_id=brand_obj.id if brand_obj else None,
                            category_id=category_obj.id if category_obj else None,
                            status="published" if stock > 0 else "pending",
                            photo_mode="supplier",
                        )
                        s.add(new_product)
                        created += 1

                    synced += 1

                except Exception as e:
                    log.error("Ошибка синхронизации товара %s: %s", product_data.get("name"), e)
                    continue

            await s.commit()
            log.info("Синхронизация завершена: %d товаров, %d создано, %d обновлено", synced, created, updated)

    def _extract_brand(self, name: str) -> str:
        """Извлечение бренда из названия"""
        words = name.split()
        if len(words) >= 2:
            potential_brand = f"{words[0]} {words[1]}"
            # Можно добавить список известных брендов
            return potential_brand
        return "Unknown"

    def _extract_category(self, name: str) -> str:
        """Извлечение категории из названия"""
        name_lower = name.lower()

        if any(word in name_lower for word in ["футболка", "tee", "t-shirt"]):
            return "Футболки"
        elif any(word in name_lower for word in ["худи", "hoodie"]):
            return "Худи"
        elif any(word in name_lower for word in ["лонгслив", "longsleeve"]):
            return "Лонгсливы"
        elif any(word in name_lower for word in ["штаны", "pants", "джинсы"]):
            return "Штаны"
        elif any(word in name_lower for word in ["кроссовки", "sneakers"]):
            return "Кроссовки"
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


async def parse_supplier(public_url: str):
    """Главная функция парсинга поставщика"""
    parser = SupplierParser(public_url)
    products = await parser.parse_with_playwright()

    if products:
        parser.products = products
        await parser.sync_to_database()
        return len(products)
    else:
        return 0
