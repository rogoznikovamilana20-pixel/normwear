# Интеграция NORMWEAR с МойСклад (API)

## Что нужно для интеграции

### 1. Получить доступ к API МойСклад

1. **Авторизация в МойСклад**
   - Зайди в https://online.moysklad.ru
   - Настройки → Разработчикам → API Access Tokens
   - Создай новый токен

2. **Параметры API**
   - Base URL: `https://online.moysklad.ru/api/remap/1.2`
   - Токен: будет в настройках
   - Entity: `assortment` (ассортимент) или `product` (товары)

### 2. Конечные точки API

**Получить все товары:**
```
GET https://online.moysklad.ru/api/remap/1.2/entity/assortment
```

**Получить остатки:**
```
GET https://online.moysklad.ru/api/remap/1.2/entity/assortment?stockMode=all
```

**Фильтр по наличию:**
```
GET https://online.moysklad.ru/api/remap/1.2/entity/assortment?filter=stock>0
```

## Интеграция в NORMWEAR

### Новый сервис: `app/services/moysklad.py`

```python
import httpx
from typing import List, Dict
from app.config import get_settings
from app.services.photos import yandex_library
from app.db import SessionMaker
from app.models import Product, Brand, Category, ProductPhoto

settings = get_settings()

class MoyskladClient:
    def __init__(self):
        self.base_url = "https://online.moysklad.ru/api/remap/1.2"
        self.token = settings.moysklad_token  # добавить в config.py
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
        )

    async def get_assortment(self) -> List[Dict]:
        """Получить весь ассортимент с остатками"""
        response = await self.client.get(
            f"{self.base_url}/entity/assortment",
            params={"stockMode": "all"}
        )
        response.raise_for_status()
        return response.json().get("rows", [])

    async def sync_products(self):
        """Синхронизировать товары с БД"""
        assortment = await self.get_assortment()

        async with SessionMaker() as s:
            for item in assortment:
                # Парсинг данных из МойСклад
                product_name = item.get("name", "")
                supplier_price = item.get("salePrices", [{}])[0].get("value", 0) / 100  # в копейках
                stock = item.get("stock", 0)
                article = item.get("article", "")

                # Наценка 35%
                retail_price = self.calculate_retail_price(supplier_price)

                # Создание/обновление товара
                # ... логика обновления БД

        await self.client.aclose()

    def calculate_retail_price(self, supplier_price: float) -> int:
        """Расчёт розничной цены с наценкой 35%"""
        margin = settings.default_margin_pct  # 35
        retail = supplier_price * (1 + margin / 100)
        return round(retail / 50) * 50  # округление до 50 ₽
```

### Обновление `app/config.py`

Добавить:
```python
moysklad_token: str | None = None
```

### Обновление `app/main.py`

Добавить задачу для автоматической синхронизации:
```python
# Автосинхронизация с МойСклад каждые 6 часов
if settings.moysklad_token:
    try:
        from app.services import moysklad as moysklad_svc
        tasks.append(asyncio.create_task(moysklad_svc.sync_loop()))
        log.info("Moysklad sync enabled")
    except Exception as e:
        log.warning("Moysklad sync disabled: %s", e)
```

## Настройка в Render

В Environment Variables добавить:
```
MOYSKLAD_TOKEN = твой_токен_от_МойСклад
```

## Преимущества интеграции

### ✅ Автоматическое обновление
- Остатки обновляются каждые 6 часов
- Цены всегда актуальны
- Новые товары появляются автоматически

### ✅ Корректная наценка
- Автоматический расчёт цены +35%
- Округление до 50 ₽
- Учёт скидок поставщика

### ✅ Синхронизация наличия
- Товары с 0 остатком скрываются
- При поступлении - появляются снова
- Предотвращение продаж отсутствующих товаров

## Альтернативные варианты

### Вариант 2: Excel/CSV экспорт
Если API недоступен:
1. Экспортировать из МойСклад в CSV
2. Загружать CSV через админ-бота
3. Парсить и обновлять БД

### Вариант 3: Webhook
МойСклад может отправлять webhooks при изменениях:
- Настроить webhook в МойСклад
- Добавить endpoint в `app/api.py`
- Обновлять БД при изменениях

## Требования

Для работы нужен:
1. ✅ Токен API МойСклад
2. ✅ Добавить в `.env`: `MOYSKLAD_TOKEN=...`
3. ✅ Обновить зависимости (если нужно)
4. ✅ Перезапустить деплой

## Тестирование

После настройки:
1. Проверить получение данных из API
2. Проверить расчёт цен
3. Проверить обновление остатков
4. Проверить создание новых товаров

## Поддержка

Документация API МойСклад:
https://dev.moysklad.ru/doc/api/remap/1.2/
