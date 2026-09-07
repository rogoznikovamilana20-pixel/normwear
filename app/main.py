import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.services import notify
from app.api import create_app
from app.bots import admin as admin_module
from app.bots import shop as shop_module
from app.config import BASE_DIR, get_settings
from app.db import SessionMaker, init_db
from app.services.photos import yandex_library
from app.services.seed import seed_basic

settings = get_settings()

# гарантируем наличие папки логов (в Docker её нет)
(BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "logs" / "app.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")

SHOP_COMMANDS = [
    BotCommand(command="start", description="Магазин NORMWEAR"),
    BotCommand(command="faq", description="Частые вопросы"),
]
ADMIN_COMMANDS = [
    BotCommand(command="admin", description="Панель управления"),
    BotCommand(command="orders", description="Активные заказы"),
    BotCommand(command="promo", description="Создать промокод"),
]


async def run_all() -> None:
    await init_db()
    brands_loaded = yandex_library.load()
    log.info("Yandex library loaded: %s brands", brands_loaded)
    async with SessionMaker() as s:
        await seed_basic(s)
        log.info("Seed done")

    # Боты создаём только если есть валидные токены — иначе web only (Render без секретов не должен падать)
    shop = None
    admin = None
    if settings.shop_bot_token and len(settings.shop_bot_token.split(":")) == 2:
        try:
            shop = Bot(settings.shop_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        except Exception as e:
            log.warning("shop Bot init failed: %s", e)
    if settings.admin_bot_token and len(settings.admin_bot_token.split(":")) == 2:
        try:
            admin = Bot(settings.admin_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        except Exception as e:
            log.warning("admin Bot init failed: %s", e)
    notify.shop_bot = shop
    notify.admin_bot = admin
    admin_module.known_brands = set(yandex_library.brand_titles)

    # Redis для FSM если доступен, иначе MemoryStorage (для бесплатного тарифа ок)
    storage_shop = MemoryStorage()
    storage_admin = MemoryStorage()
    if settings.redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            from redis.asyncio import Redis

            redis = Redis.from_url(settings.redis_url, decode_responses=False)
            await redis.ping()
            storage_shop = RedisStorage(redis)
            storage_admin = RedisStorage(redis)
            log.info("Redis FSM enabled: %s", settings.redis_url.split("@")[-1])
        except Exception as e:
            log.warning("Redis unavailable, fallback to MemoryStorage: %s", e)

    d_shop = Dispatcher(storage=storage_shop)
    d_admin = Dispatcher(storage=storage_admin)
    d_shop.include_router(shop_module.router)
    d_admin.include_router(admin_module.router)

    app = create_app()
    port = settings.effective_port
    log.info("Starting web on 0.0.0.0:%s (env=%s)", port, settings.app_env)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info"))
    tasks = [asyncio.create_task(server.serve())]
    if not settings.run_bots:
        log.info("RUN_BOTS=false: web only")
    elif shop is None or admin is None:
        log.warning("BOT TOKENS пустые/невалидные — боты не запустятся, только веб")
    else:
        # set_my_commands с таймаутом 5с — не вешаем старт веба
        try:
            await asyncio.wait_for(shop.set_my_commands(SHOP_COMMANDS), timeout=5)
        except Exception as e:
            log.warning("shop set_my_commands failed: %s", e)
        # Синяя кнопка Menu слева от ввода -> открывает мини-апп сразу
        try:
            from aiogram.types import MenuButtonWebApp, WebAppInfo

            miniapp_base = (settings.miniapp_url_template or "").split("?")[0].replace("/app/", "/").rstrip("/") + "/"
            if not miniapp_base.startswith("https://"):
                miniapp_base = "https://normwear-shop.onrender.com/"
            await asyncio.wait_for(
                shop.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🛍 Каталог", web_app=WebAppInfo(url=miniapp_base))),
                timeout=5,
            )
            log.info("Shop menu button -> %s", miniapp_base)
        except Exception as e:
            log.warning("shop menu button failed: %s", e)
        try:
            await asyncio.wait_for(admin.set_my_commands(ADMIN_COMMANDS), timeout=5)
        except Exception as e:
            log.warning("admin set_my_commands failed: %s", e)
        try:
            me = await asyncio.wait_for(shop.get_me(), timeout=5)
            log.info("Shop bot polling as @%s", me.username)
        except Exception as e:
            log.error("Shop bot get_me failed: %s", e)
        try:
            me2 = await asyncio.wait_for(admin.get_me(), timeout=5)
            log.info("Admin bot polling as @%s", me2.username)
        except Exception as e:
            log.error("Admin bot get_me failed: %s", e)
        tasks.append(asyncio.create_task(d_shop.start_polling(shop, handle_signals=False)))
        tasks.append(asyncio.create_task(d_admin.start_polling(admin, handle_signals=False)))
    # Фаза 9: автопарсинг поставщика (не блокирует старт, рестартует сам)
    try:
        from app.services import supplier_watcher

        if supplier_watcher.is_configured(settings):
            tasks.append(asyncio.create_task(supplier_watcher.run_forever(settings, admin_module.process_supplier_auto)))
            log.info("Supplier watcher enabled: @%s", settings.supplier_channel_username)
        else:
            log.info("Supplier watcher off (нет API_ID/HASH/сессии)")
    except Exception as e:
        log.warning("Supplier watcher disabled: %s", e)
    # Дожим корзин + винбэк (только где живут боты)
    if shop is not None:
        try:
            from app.services import retention as retention_svc

            tasks.append(asyncio.create_task(retention_svc.abandoned_cart_loop()))
            tasks.append(asyncio.create_task(retention_svc.winback_loop()))
            log.info("Retention loops enabled")
        except Exception as e:
            log.warning("Retention disabled: %s", e)
    try:
        await asyncio.gather(*tasks)
    finally:
        # корректно закрываем httpx клиент Я.Диска
        try:
            await yandex_library.aclose()
        except Exception:
            pass
        if shop is not None:
            try:
                await shop.session.close()
            except Exception:
                pass
        if admin is not None:
            try:
                await admin.session.close()
            except Exception:
                pass


def check() -> None:
    asyncio.run(init_db())
    yandex_library.load()
    app = create_app()
    print("check ok: routes =", len(app.routes))
    from app.services.parser import parse_product

    parsed = parse_product("Nike Dunk Low\nРазмеры: 40 41 42 43\nЦена: 7500 руб\nВ наличии: 3 шт")
    assert parsed is not None and parsed.supplier_price == 7500 and parsed.brand == "Nike", parsed
    print("parser ok:", parsed.title, parsed.supplier_price, parsed.brand, parsed.sizes)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        asyncio.run(run_all())