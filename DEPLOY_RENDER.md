# Деплой NORMWEAR на Render.com (бесплатно)

## Предварительная настройка

### 1. Создание аккаунта на Render
1. Перейди на https://render.com
2. Зарегистрируйся через GitHub (проще всего)
3. Подключи GitHub репозиторий `rogoznikovamilana20-pixel/normwear`

### 2. Подготовка переменных окружения
Скопируй эти значения для настройки в Render (они будут в Environment Variables):

**Обязательные:**
- `SHOP_BOT_TOKEN` = токен вашего магазин-бота от @BotFather
- `ADMIN_BOT_TOKEN` = токен админ-бота от @BotFather
- `SHOP_CHANNEL_ID` = `-1004387729213` (или ID вашего канала)
- `ADMIN_TELEGRAM_IDS` = `1977604257` (ваш Telegram ID)
- `SHOP_CHANNEL_USERNAME` = `normwear_shop`
- `SHOP_USERNAME` = `norm_shop_bot`

**Опциональные (но рекомендуемые):**
- `SUPPLIER_CHANNEL_USERNAME` = `optobaza`
- `DEFAULT_MARGIN_PCT` = `35`
- `YANDEX_PUBLIC_KEY` = `https://disk.yandex.ru/d/e4YGRLhebhBoVA`
- `RUN_BOTS` = `true`

## Пошаговый деплой

### Шаг 1: Создание Web Service
1. В Render dashboard → **New +** → **Blueprint**
2. Выбери репозиторий `rogoznikovamilana20-pixel/normwear`
3. Render автоматически найдёт `render.yaml` и предложит конфигурацию
4. Нажми **Apply**

### Шаг 2: Настройка Environment Variables
1. После создания Blueprint зайди в созданный Web Service
2. Перейди в **Environment**
3. Добавь переменные из списка выше
4. **Важно:** `DATABASE_URL` не трогай - Render подключит PostgreSQL автоматически через `render.yaml`

### Шаг 3: Деплой
1. Нажми **Manual Deploy** → **Clear build cache & deploy**
2. Жди завершения (обычно 3-5 минут)
3. В логах должно появиться:
   - `Yandex library loaded: 38 brands`
   - `Shop bot polling as @norm_shop_bot`
   - `Starting web on 0.0.0.0:8000`

### Шаг 4: Проверка работоспособности
1. Открой URL вашего приложения (пример: `https://normwear-shop.onrender.com`)
2. Проверь эндпоинты:
   - `https://normwear-shop.onrender.com/healthz` → `{"ok":true}`
   - `https://normwear-shop.onrender.com/api/catalog` → список товаров
3. Протестируй бота:
   - Открой `@norm_shop_bot`
   - Нажми `/start`
   - Проверь каталог и корзину

## Поддержание активности (Render спит через 15 мин)

Render Free Tier спит через 15 минут неактивности. Чтобы бот работал 24/7:

### Вариант 1: UptimeRobot (рекомендую)
1. Зарегистрируйся на https://uptimerobot.com
2. **New Monitor** → **HTTP(s)**
3. URL: `https://normwear-shop.onrender.com/healthz`
4. Interval: `5 minutes`
5. Название: `NORMWEAR Health Check`
6. Сохранить

### Вариант 2: Встроенный пингер
В репозитории есть файлы `ping_render.bat` и `ping_render.pyw` - можно запускать с домашнего компьютера.

## Настройка мини-приложения

После успешного деплоя обнови ссылку на мини-приложение:

1. В Render Environment добавь:
   - `MINIAPP_URL_TEMPLATE` = `https://normwear-shop.onrender.com/`

2. Перезапусти деплой (Manual Deploy)

3. В боте появится кнопка **✨ Мини-каталог** которая открывает Web App

## Резервное копирование

### PostgreSQL бэкапы
Render автоматически создаёт бэкапы PostgreSQL (ежедневно в бесплатном тарифе).

Проверить бэкапы:
1. Render Dashboard → Databases → `normwear-db`
2. Вкладка **Backups**

### Ручной бэкап
```bash
# Локально (если нужно)
pg_dump $DATABASE_URL > backup.sql
```

## Мониторинг

### Логи приложения
1. Render Dashboard → Web Service → **Logs**
2. Следи за ошибками и предупреждениями

### Метрики
1. Render Dashboard → Web Service → **Metrics**
2. Следи за CPU, Memory, Response time

## Troubleshooting

### Бот не отвечает
1. Проверь логи в Render (Errors)
2. Убедись что токены ботов правильные
3. Проверь что `RUN_BOTS=true`

### База данных не подключается
1. Проверь что PostgreSQL сервис активен
2. Проверь `DATABASE_URL` в Environment Variables

### Мини-приложение не открывается
1. Проверь что `MINIAPP_URL_TEMPLATE` правильный
2. Убедись что URL начинается с `https://`

### Render спит несмотря на пингер
1. Проверь что UptimeRobot активен
2. Увеличь интервал пинга до 3 минут
3. Попробуй другой пингер (например, Pingdom)

## Дополнительные функции

### VK авто-постинг (опционально)
Если нужен авто-постинг в VK:
1. Создай VK приложение и получи токен
2. Добавь в Environment:
   - `VK_TOKEN` = твой VK токен
   - `VK_GROUP_ID` = ID группы
3. Перезапусти деплой

### Телеграм парсинг поставщика (опционально)
Для автопарсинга канала поставщика:
1. Получи `api_id` и `api_hash` на https://my.telegram.org
2. Создай Telethon сессию
3. Добавь в Environment:
   - `TELEGRAM_API_ID` = твой API ID
   - `TELEGRAM_API_HASH` = твой API Hash
   - `SUPPLIER_SESSION_STRING` = сессия Telethon
4. Перезапусти деплой

## Обновление проекта

Для обновления после изменений в коде:

1. Сделай изменения локально
2. Закоммить и запуши в GitHub:
   ```bash
   git add .
   git commit -m "Описание изменений"
   git push origin main
   ```
3. Render автоматически задеплоит новые изменения

## Поддержка

Если возникнут проблемы:
1. Проверь логи в Render
2. Проверь переменные окружения
3. Убедись что GitHub репозиторий синхронизирован
4. Свяжись с поддержкой Render

## Стоимость

**Бесплатный тариф Render:**
- Web Service: 750 часов/месяц (хватает для 24/7)
- PostgreSQL: 90 дней бесплатно, потом $7/месяц
- Bandwidth: 100GB/месяц

Если превысишь лимиты - бот будет временно недоступен до следующего месяца.
