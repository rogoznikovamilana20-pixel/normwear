# AGENTS.md — правила проекта NORMWEAR

## Стек и архитектура
- Python 3.12, aiogram 3.22 (long polling, НЕ webhooks), FastAPI+uvicorn (API + мини-апп), SQLAlchemy 2 async (aiosqlite dev / asyncpg prod), httpx, pydantic-settings.
- Структура: `app/config.py` (Settings), `app/db.py`, `app/models.py` (все таблицы), `app/services/*` (парсер, цены, фото Я.Диска, публикатор, заказы, уведомления, сид), `app/bots/shop.py`, `app/bots/admin.py`, `app/api.py`, `app/main.py`, `static/`, `data/`, `logs/`.
- Модули держать ≤300 строк; одноразовые скрипты в корне не оставлять.

## Кодинг-правила
- datetime: только naive UTC через `utcnow()` из `app.models` (timezone-aware не смешивать).
- aiogram 3: фильтры через magic `F` (`F.forward_origin`, `F.media_group_id`, `F.text & ~F.command`); FSM через StatesGroup; роутеры: shop и admin раздельные, у admin — router-level фильтр по `settings.admin_ids`.
- file_id из форвардов валидны только для того бота, который их получил (публикация в канал идёт от админ-бота).
- Статусы заказа: awaiting_delivery → awaiting_payment → shipped → delivered → completed (+cancelled); `ORDER_NEXT` в models.
- Наценка: `retail_price()` в `app/services/pricing.py`, округление до 50 ₽.
- Секреты ТОЛЬКО в `.env` (в коде/доках/коммитах их не будет; `.gitignore` уже исключает). Токен в чате = скомпрометирован → ротация через @BotFather.

## Команды
- venv: `python -m venv .venv`; зависимости: `.venv\Scripts\pip install -r requirements.txt`
- проверка: `.venv\Scripts\python -m app.main --check`
- запуск: `start.bat` или `.venv\Scripts\python -m app.main`
- только веб (без ботов): env `RUN_BOTS=false`

## Взаимодействие
- Пользователь — новичок: объяснять просто, язык — русский.
- База локально: `normwear.db` (SQLite). Не коммитить базу и `.env`.
- git-коммиты — только по явной просьбе пользователя.