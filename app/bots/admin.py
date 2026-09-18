import asyncio
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionMaker
from app.models import (
    AdminAudit,
    Brand,
    Category,
    GiveawayEntry,
    Order,
    OrderItem,
    Product,
    ProductPhoto,
    PromoCode,
    SupportMessage,
    SupportTicket,
    User,
    utcnow,
)
from app.services import notify, orders
from app.services.parser import parse_product
from app.services.photos import yandex_library
from app.services.pricing import retail_price
from app.services.publisher import publish_product

router = Router()
settings = get_settings()

known_brands: set[str] = set()
_groups: dict[str, dict] = {}
pending_reply: dict[int, int] = {}


def _is_admin_msg(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


def _is_admin_cb(cb: CallbackQuery) -> bool:
    return cb.from_user.id in settings.admin_ids


router.message.filter(_is_admin_msg)
router.callback_query.filter(_is_admin_cb)


class AdminFSM(StatesGroup):
    price = State()
    tracking = State()
    broadcast = State()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Заказы", callback_data="orders"), InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="🗂 На модерации", callback_data="pending")],
            [InlineKeyboardButton(text="🔄 Парсить поставщика", callback_data="parse_supplier"), InlineKeyboardButton(text="📡 Статус watcher'а", callback_data="watcher_status")],
        ]
    )
    await message.answer(
        "🛠 <b>Админ-панель NORMWEAR</b>\n\n"
        f"📩 Просто перешлите пост из @{settings.supplier_channel_username} в этот чат — бот распарсит товар и покажет карточку.\n\n"
        "Команды:\n"
        "/orders — заказы\n"
        "/parse_supplier — парсинг поставщика\n"
        "/test_parser — тест парсера\n"
        "/watcher_status — статус watcher'а\n"
        "/promo CODE PERCENT [MIN] [MAXUSES] — промокод\n"
        "/draw — финал розыгрыша\n"
        "/digest — сводка",
        reply_markup=kb,
    )


@router.message(Command("orders"))
async def cmd_orders(message: Message):
    await send_orders_list(message)


@router.message(Command("parse_supplier"))
async def cmd_parse_supplier(message: Message):
    """Парсинг товаров с публичной страницы поставщика"""
    supplier_url = "https://b2b.moysklad.ru/public/oWXBoG49bkuB"

    await message.answer("🔄 Начинаю парсинг товаров с поставщика...")

    try:
        from app.services.supplier_parser import parse_supplier

        count = await parse_supplier(supplier_url)

        if count > 0:
            await message.answer(f"✅ Успешно распарсено и добавлено {count} товаров с наценкой 35%")
        else:
            await message.answer("⚠️ Не удалось распарсить товары. Проверьте ссылку поставщика.")

    except ImportError:
        await message.answer("❌ Playwright не установлен. Установите: pip install playwright && playwright install chromium")
    except Exception as e:
        await message.answer(f"❌ Ошибка парсинга: {str(e)}")


@router.message(Command("test_parser"))
async def cmd_test_parser(message: Message):
    """Тестирование парсера товаров"""
    test_text = """Nike Dunk Low
Размеры: 40 41 42 43
Цена: 7500 руб
В наличии: 3 шт
Артикул: NK123"""

    from app.services.parser import parse_product

    parsed = parse_product(test_text, known_brands=sorted(known_brands))

    if parsed:
        await message.answer(
            f"✅ Парсер работает!\n\n"
            f"📦 Товар: {parsed.title}\n"
            f"🏷️ Бренд: {parsed.brand or 'Не определён'}\n"
            f"💰 Цена поставщика: {parsed.supplier_price}₽\n"
            f"📏 Размеры: {', '.join(parsed.sizes) if parsed.sizes else 'Нет'}\n"
            f"📦 Остаток: {parsed.stock} шт\n"
            f"🏷️ Артикул: {parsed.article or 'Нет'}\n"
            f"📂 Категория: {parsed.category or 'Не определена'}"
        )
    else:
        await message.answer("❌ Парсер не смог распознать товар")


@router.message(Command("watcher_status"))
async def cmd_watcher_status(message: Message):
    """Проверка статуса watcher'а канала поставщика"""
    from app.config import get_settings

    settings = get_settings()

    status_text = f"📊 <b>Статус Watcher'а канала поставщика</b>\n\n"

    # Проверка настроек
    if settings.telegram_api_id and settings.telegram_api_hash and settings.supplier_session_string:
        status_text += "✅ Настройки Telethon: настроены\n"
    else:
        status_text += "❌ Настройки Telethon: не настроены\n"

    if settings.supplier_channel_username:
        status_text += f"✅ Канал поставщика: @{settings.supplier_channel_username}\n"
    else:
        status_text += "❌ Канал поставщика: не настроен\n"

    # Проверка активности
    status_text += f"\n📡 Канал: @{settings.supplier_channel_username}\n"
    status_text += f"🔑 API ID: {settings.telegram_api_id}\n"
    status_text += f"📱 Сессия: {'Активна' if settings.supplier_session_string else 'Не настроена'}\n"

    status_text += "\n\n💡 Для ручного тестирования перешли пост из канала в этот чат"

    await message.answer(status_text)


@router.callback_query(F.data == "orders")
async def cb_orders(cb: CallbackQuery):
    await send_orders_list(cb.message)
    await cb.answer()


@router.callback_query(F.data == "parse_supplier")
async def cb_parse_supplier(cb: CallbackQuery):
    """Парсинг товаров с публичной страницы поставщика"""
    supplier_url = "https://b2b.moysklad.ru/public/oWXBoG49bkuB"

    await cb.message.answer("🔄 Начинаю парсинг товаров с поставщика...")

    try:
        from app.services.supplier_parser import parse_supplier

        count = await parse_supplier(supplier_url)

        if count > 0:
            await cb.message.answer(f"✅ Успешно распарсено и добавлено {count} товаров с наценкой 35%")
        else:
            await cb.message.answer("⚠️ Не удалось распарсить товары. Проверьте ссылку поставщика.")

    except ImportError:
        await cb.message.answer("❌ Playwright не установлен. Установите: pip install playwright && playwright install chromium")
    except Exception as e:
        await cb.message.answer(f"❌ Ошибка парсинга: {str(e)}")

    await cb.answer()


@router.callback_query(F.data == "watcher_status")
async def cb_watcher_status(cb: CallbackQuery):
    """Проверка статуса watcher'а канала поставщика"""
    from app.config import get_settings

    settings = get_settings()

    status_text = f"📊 <b>Статус Watcher'а канала поставщика</b>\n\n"

    # Проверка настроек
    if settings.telegram_api_id and settings.telegram_api_hash and settings.supplier_session_string:
        status_text += "✅ Настройки Telethon: настроены\n"
    else:
        status_text += "❌ Настройки Telethon: не настроены\n"

    if settings.supplier_channel_username:
        status_text += f"✅ Канал поставщика: @{settings.supplier_channel_username}\n"
    else:
        status_text += "❌ Канал поставщика: не настроен\n"

    # Проверка активности
    status_text += f"\n📡 Канал: @{settings.supplier_channel_username}\n"
    status_text += f"🔑 API ID: {settings.telegram_api_id}\n"
    status_text += f"📱 Сессия: {'Активна' if settings.supplier_session_string else 'Не настроена'}\n"

    status_text += "\n\n💡 Для ручного тестирования перешли пост из канала в этот чат"

    await cb.message.answer(status_text)
    await cb.answer()


async def send_orders_list(target: Message):
    async with SessionMaker() as s:
        ords = (
            await s.scalars(
                select(Order)
                .where(Order.status.in_(("awaiting_delivery", "awaiting_payment", "shipped")))
                .order_by(Order.id.desc())
                .limit(10)
            )
        ).all()
    if not ords:
        await target.answer("Активных заказов нет ✅")
        return
    rows = [
        [InlineKeyboardButton(text=f"№{o.id} · {notify.ORDER_STATUS_LABEL.get(o.status, o.status)} · {int(o.total)} ₽", callback_data=f"od:{o.id}")]
        for o in ords
    ]
    await target.answer("📦 <b>Активные заказы</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    async with SessionMaker() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
        prods = (await s.execute(select(func.count()).select_from(Product).where(Product.status == "published"))).scalar() or 0
        pend = (await s.execute(select(func.count()).select_from(Product).where(Product.status == "pending"))).scalar() or 0
        ords = (await s.execute(select(func.count()).select_from(Order))).scalar() or 0
        rev = (await s.execute(select(func.coalesce(func.sum(Order.total), 0.0)).where(Order.status == "completed"))).scalar() or 0
    await cb.message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Клиентов: {users}\n"
        f"🛍 Опубликовано товаров: {prods}\n"
        f"⏳ На модерации: {pend}\n"
        f"📦 Заказов всего: {ords}\n"
        f"💰 Выручка (завершённые): {int(rev)} ₽"
    )
    await cb.answer()


@router.callback_query(F.data == "pending")
async def cb_pending(cb: CallbackQuery):
    async with SessionMaker() as s:
        prods = (await s.scalars(select(Product).where(Product.status == "pending").order_by(Product.id.desc()).limit(10))).all()
        ids = [p.id for p in prods]
    if not ids:
        await cb.answer("Нет товаров на модерации", show_alert=True)
        return
    for pid in ids:
        await send_admin_product(cb.from_user.id, pid)
    await cb.answer()


@router.callback_query(F.data.startswith("od:"))
async def cb_order(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    oid = int(parts[1])
    action = parts[2] if len(parts) > 2 else ""
    if action == "tr":
        await state.set_state(AdminFSM.tracking)
        await state.update_data(order_id=oid)
        await cb.message.answer("📮 Введите трек-номер:")
        await cb.answer()
        return
    if action == "pay":
        from app.models import OrderItem as _OI
        from app.services import payments as payments_svc

        async with SessionMaker() as s:
            order = await s.get(Order, oid)
            if order is None:
                await cb.answer("Заказ не найден", show_alert=True)
                return
            items = (await s.scalars(select(_OI).where(_OI.order_id == oid))).all()
            items_text = ", ".join(f"{it.title}×{it.qty}" for it in items)[:200]
        bot = notify.shop_bot
        ok = await payments_svc.send_order_invoice(bot, settings, order, items_text) if bot else False
        await send_order_card(cb.message, oid, note="Счёт отправлен клиенту" if ok else "Провайдер оплаты не настроен — кидай реквизиты вручную")
    elif action == "next":
        async with SessionMaker() as s:
            order = await orders.advance_order(s, oid, changed_by=cb.from_user.id)
        if order is not None:
            await notify.notify_user(order.user_id, notify.order_status_text(order))
        await send_order_card(cb.message, oid, note="Статус обновлён" if order else "Не удалось обновить")
    elif action == "cn":
        async with SessionMaker() as s:
            order = await orders.advance_order(s, oid, to_status="cancelled", changed_by=cb.from_user.id)
        if order is not None:
            await notify.notify_user(order.user_id, notify.order_status_text(order))
        await send_order_card(cb.message, oid, note="Заказ отменён")
    else:
        await send_order_card(cb.message, oid)
    await cb.answer()


async def send_order_card(target: Message, order_id: int, note: str | None = None):
    async with SessionMaker() as s:
        order = await s.get(Order, order_id)
        if order is None:
            await target.answer("Заказ не найден")
            return
        user = await s.get(User, order.user_id)
        items = (await s.scalars(select(OrderItem).where(OrderItem.order_id == order_id))).all()
    uname = f"@{user.username}" if user and user.username else "—"
    lines = [
        f"📦 <b>Заказ №{order.id}</b> · {notify.ORDER_STATUS_LABEL.get(order.status, order.status)}",
        f"👤 {html.escape(order.full_name)} · {uname} · id <code>{order.user_id}</code>",
        f"📱 {html.escape(order.phone)}",
        f"🏙 {html.escape(order.city)}, {html.escape(order.address)}",
        "",
    ]
    lines += [f"• {html.escape(it.title)}{(' · ' + it.size) if it.size else ''} ×{it.qty} — {int(it.price)} ₽" for it in items]
    if order.promo_code:
        lines.append(f"🎟 Промокод: {order.promo_code} (−{int(order.discount)} ₽)")
    if order.bonus_used:
        lines.append(f"🎁 Списание бонусов: −{order.bonus_used} ₽")
    lines += ["", f"💰 <b>Итого: {int(order.total)} ₽</b>"]
    if order.tracking_number:
        lines.append(f"📮 Трек: {html.escape(order.tracking_number)}")
    if note:
        lines += ["", f"ℹ️ {html.escape(note)}"]
    rows = []
    if order.status == "awaiting_delivery":
        rows.append([InlineKeyboardButton(text="➡ Готов к оплате", callback_data=f"od:{order.id}:next")])
    elif order.status == "awaiting_payment":
        from app.services import payments as payments_svc

        if payments_svc.is_configured(settings):
            rows.append([InlineKeyboardButton(text="💳 Выставить счёт", callback_data=f"od:{order.id}:pay")])
        rows.append([InlineKeyboardButton(text="🚚 Отправлен (ввести трек)", callback_data=f"od:{order.id}:tr")])
    elif order.status == "shipped":
        rows.append([InlineKeyboardButton(text="📬 Доставлен", callback_data=f"od:{order.id}:next")])
    elif order.status == "delivered":
        rows.append([InlineKeyboardButton(text="✅ Завершить", callback_data=f"od:{order.id}:next")])
    if order.status in ("awaiting_delivery", "awaiting_payment"):
        rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"od:{order.id}:cn")])
    rows.append([InlineKeyboardButton(text="⬅ К списку", callback_data="orders")])
    await target.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(AdminFSM.tracking, F.text)
async def st_tracking(message: Message, state: FSMContext):
    d = await state.get_data()
    oid = int(d.get("order_id") or 0)
    await state.clear()
    track = (message.text or "").strip()[:64]
    async with SessionMaker() as s:
        order = await orders.advance_order(s, oid, to_status="shipped", changed_by=message.from_user.id, tracking=track)
    if order is None:
        await message.answer("Заказ не найден")
        return
    await notify.notify_user(order.user_id, notify.order_status_text(order))
    await send_order_card(message, oid, note="Трек-номер сохранён")


@router.message(Command("content"))
async def cmd_content(message: Message):
    from app.services import content_plan as content_svc

    mid = await content_svc.post_next(settings, force=True)
    await message.answer(f"📝 Пост из контент-плана улетел (msg {mid})." if mid else "⚠️ Не вышло — проверь канал.")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    await state.set_state(AdminFSM.broadcast)
    await message.answer("📣 Пришли текст рассылки одним сообщением (или /cancel чтобы отменить).")


@router.message(Command("cancel"))
async def cmd_cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@router.message(AdminFSM.broadcast, F.text)
async def st_broadcast(message: Message, state: FSMContext):
    text = (message.text or "").strip()[:3000]
    await state.clear()
    if not text or text == "/cancel":
        await message.answer("Отменено.")
        return
    bot = notify.shop_bot
    if bot is None:
        await message.answer("Шоп-бот не запущен.")
        return
    async with SessionMaker() as s:
        users = (await s.scalars(select(User).where(User.is_banned == False))).all()  # noqa: E712
        ids = [u.id for u in users]
    await message.answer(f"📣 Рассылка {len(ids)} юзерам пошла...")
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            continue
        await asyncio.sleep(0.05)
    async with SessionMaker() as s:
        s.add(AdminAudit(admin_id=message.from_user.id, action="broadcast", entity="users", entity_id=None, payload={"sent": sent, "total": len(ids)}))
        await s.commit()
    await message.answer(f"✅ Разослано {sent}/{len(ids)}.")


@router.message(Command("digest"))
async def cmd_digest(message: Message):
    """Сводка: люди, деньги, воронка, розыгрыш, канал + рекомендации."""
    from datetime import timedelta

    from app.models import (
        CartItem,
        DropSubscription,
        FortuneSpin,
        Giveaway,
        GiveawayEntry,
        GiveawayReferral,
        Order,
        Review,
        ScheduledPost,
        User,
    )

    week_ago = utcnow() - timedelta(days=7)
    async with SessionMaker() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
        new7 = (await s.execute(select(func.count()).select_from(User).where(User.created_at >= week_ago))).scalar() or 0
        buyers = (await s.execute(select(func.count()).select_from(User).where(User.orders_count > 0))).scalar() or 0
        ords = (await s.scalars(select(Order))).all()
        rev = sum(int(o.total) for o in ords if o.status == "completed")
        done = [o for o in ords if o.status == "completed"]
        avg = int(rev / len(done)) if done else 0
        active = [o for o in ords if o.status not in ("completed", "cancelled")]
        active_sum = sum(int(o.total) for o in active)
        opt_n = sum(1 for o in ords if o.is_wholesale)
        carts = (await s.execute(select(func.count()).select_from(CartItem))).scalar() or 0
        drops = (await s.execute(select(func.count()).select_from(DropSubscription))).scalar() or 0
        spins = (await s.execute(select(func.count()).select_from(FortuneSpin))).scalar() or 0
        rev_total = (await s.execute(select(func.count()).select_from(Review))).scalar() or 0
        rev_unpub = (await s.execute(select(func.count()).select_from(Review).where(Review.is_published == False))).scalar() or 0  # noqa: E712
        gw = (await s.scalars(select(Giveaway).where(Giveaway.code == "give1", Giveaway.status == "active"))).first()
        sched = (await s.execute(select(func.count()).select_from(ScheduledPost).where(ScheduledPost.is_sent == False))).scalar() or 0  # noqa: E712
        g_entries = g_refs = g_tickets = g_days = 0
        if gw is not None:
            g_entries = (await s.execute(select(func.count()).select_from(GiveawayEntry).where(GiveawayEntry.giveaway_id == gw.id))).scalar() or 0
            g_refs = (await s.execute(select(func.count()).select_from(GiveawayReferral).where(GiveawayReferral.giveaway_id == gw.id))).scalar() or 0
            g_tickets = g_entries + g_refs
            if gw.ends_at:
                g_days = max(0, (gw.ends_at - utcnow()).days)
    subs = 0
    try:
        if notify.admin_bot is not None:
            subs = await notify.admin_bot.get_chat_member_count(int(settings.shop_channel_id))
    except Exception:
        pass
    conv = round(buyers / users * 100, 1) if users else 0
    lines = [
        f"📊 <b>Сводка NORMWEAR</b> · {utcnow():%d.%m %H:%M}",
        "",
        f"👥 Люди: <b>{users}</b> (+{new7} за 7 дн) · покупателей: {buyers} ({conv}%)",
        f"📢 Канал: <b>{subs}</b> подписчиков",
        f"💰 Выручка: <b>{rev}₽</b> · средний чек: {avg}₽",
        f"📦 Активных заказов: {len(active)} на {active_sum}₽ · опт-заявок всего: {opt_n}",
        f"🛒 Позиций в корзинах: {carts} · 🔔 дропы: {drops} · 🎡 крутки: {spins}",
        f"⭐️ Отзывов: {rev_total} (на модерации: {rev_unpub})",
    ]
    if gw is not None:
        lines.append(f"🎲 Розыгрыш: {g_entries} уч. · {g_tickets} билетов · {g_refs} друзей · финал через {g_days} дн.")
    if sched:
        lines.append(f"🕓 Отложенных постов: {sched}")
    recs = []
    if rev_unpub:
        recs.append(f"⭐️ На модерации {rev_unpub} отзыв(а) — опубликуй, это доверие и продажи")
    if gw is not None and g_days <= 3:
        recs.append(f"🎲 Финал через {g_days} дн — проверь призы и подписки, будет /draw")
    elif gw is not None and g_entries < 20:
        recs.append("🎲 Участников мало — репосты розыгрыша по чатам и communities")
    if not opt_n:
        recs.append("🏭 Опт-заявок ноль — разошли прайс по чатам перекупов")
    if carts and not active:
        recs.append(f"🛒 {carts} поз. в корзинах без заказов — добей рассылкой")
    if not new7:
        recs.append("📉 Притока нет 7 дней — нужен внешний трафик (партнёрки, посевы)")
    if users >= 20 and conv < 5:
        recs.append("🔄 Конверсия ниже 5% — глянь отзывы и распродажу")
    if not recs:
        recs.append("✅ Всё ровно — держи темп: контент-план идёт сам")
    lines += ["", "💡 <b>Рекомендации:</b>"] + [f"• {r}" for r in recs[:4]]
    await message.answer("\n".join(lines))


@router.message(Command("crm"))
async def cmd_crm(message: Message):
    async with SessionMaker() as s:
        from sqlalchemy import func

        from app.models import BoxSubscription, BrandSubscription, DropSubscription, FortuneSpin, Order, Review, User

        users = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
        orders = (await s.execute(select(func.count()).select_from(Order))).scalar() or 0
        rev = (await s.execute(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status == "completed"))).scalar() or 0
        subs_drop = (await s.execute(select(func.count()).select_from(DropSubscription))).scalar() or 0
        subs_brand = (await s.execute(select(func.count()).select_from(BrandSubscription))).scalar() or 0
        subs_box = (await s.execute(select(func.count()).select_from(BoxSubscription).where(BoxSubscription.is_active == True))).scalar() or 0
        spins = (await s.execute(select(func.count()).select_from(FortuneSpin))).scalar() or 0
        reviews = (await s.execute(select(func.count()).select_from(Review))).scalar() or 0
        # воронка
        carts = (await s.execute(select(func.count()).select_from(User).where(User.orders_count == 0))).scalar() or 0
    text = (
        "📊 <b>CRM NORMWEAR</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"📦 Заказов: {orders} (выручка {int(rev)}₽)\n"
        f"🔔 Дропы: {subs_drop} | Бренды: {subs_brand} | BOX: {subs_box}\n"
        f"🎡 Круток: {spins} | ⭐️ Отзывов: {reviews}\n"
        f"🛒 Корзин без заказа: ~{carts}\n\n"
        "Воронка: Зашли → Подписались → Крутили → Заказали → Отзыв"
    )
    await message.answer(text)


@router.message(Command("promo"))
async def cmd_promo(message: Message):
    args = (message.text or "").split()[1:]
    if len(args) < 2:
        await message.answer("Формат: /promo CODE PERCENT [MIN_ORDER] [MAX_USES]\nПример: /promo SUMMER10 10 5000 100")
        return
    code = args[0].upper()[:32]
    try:
        value = float(args[1])
    except ValueError:
        await message.answer("PERCENT — число, например 10")
        return
    min_order = float(args[2]) if len(args) > 2 and args[2].replace(".", "").isdigit() else 0.0
    max_uses = int(args[3]) if len(args) > 3 and args[3].isdigit() else None
    async with SessionMaker() as s:
        if (await s.scalars(select(PromoCode).where(PromoCode.code == code))).first() is not None:
            await message.answer("Такой промокод уже есть")
            return
        s.add(PromoCode(code=code, kind="percent", value=value, min_order=min_order, max_uses=max_uses))
        await s.commit()
    lim = f", лимит {max_uses} активаций" if max_uses else ""
    await message.answer(f"🎟 Промокод <code>{code}</code>: −{value:g}% от {int(min_order)} ₽{lim}")


@router.message(Command("optprice"))
async def cmd_optprice(message: Message):
    """Опт-прайс: /optprice АРТИКУЛ [КОЛ-ВО] · /optprice all — весь прайс файлом."""
    from aiogram.types import BufferedInputFile

    from app.services import opt as opt_svc

    args = (message.text or "").split()[1:]
    if not args:
        await message.answer(
            "🏭 <b>Опт-прайс</b>\n\n"
            f"Ступени: {opt_svc.tiers_text()}\n\n"
            "/optprice <code>АРТИКУЛ [КОЛ-ВО]</code> — цены по ступеням\n"
            "/optprice <code>all</code> — весь прайс файлом"
        )
        return
    if args[0].lower() == "all":
        raw = await opt_svc.pricelist_csv()
        try:
            from app.config import BASE_DIR

            (BASE_DIR / "data" / "opt_price.csv").write_bytes(raw)
        except Exception:
            pass
        await message.answer_document(BufferedInputFile(raw, filename="opt_price.csv"), caption="📄 Опт-прайс (внутренний, не пересылать)")
        return
    article = args[0].upper()
    try:
        qty = int(args[1]) if len(args) > 1 else 10
    except ValueError:
        qty = 10
    cat = await opt_svc.article_catalog()
    p = cat.get(article)
    if p is None:
        await message.answer("Нет такого артикула в опубликованном.")
        return
    base = float(p.supplier_price or 0)
    await message.answer(
        f"🏭 <b>{html.escape(article)}</b> — {html.escape(p.title[:80])}\n\n"
        f"{opt_svc.tier_lines(base)}\n"
        f"Розница: <b>{int(p.retail_price or 0)}₽</b> · кол-во в запросе: {qty} шт"
    )


@router.message(Command("scheduled"))
async def cmd_scheduled(message: Message):
    """Очередь отложенных постов."""
    from app.models import ScheduledPost
    from app.services import scheduled as scheduled_svc

    async with SessionMaker() as s:
        rows = await scheduled_svc.pending(s)
    if not rows:
        await message.answer("📭 Очередь отложенных постов пуста")
        return
    lines = ["🕓 <b>Отложенные посты:</b>", ""]
    for r in rows:
        lines.append(f"#{r.id} · {r.send_at:%d.%m %H:%M} UTC · {r.text[:60]}")
    await message.answer("\n".join(lines))


@router.message(Command("draw"))
async def cmd_draw(message: Message):
    """Финал розыгрыша: /draw — 3 победителя среди подписчиков + купоны 2-3 места."""
    from app.services import giveaway as giveaway_svc

    async with SessionMaker() as s:
        gw = await giveaway_svc.active(s)
        if gw is None:
            await message.answer("Активных розыгрышей нет")
            return
        n = (await s.execute(select(func.count()).select_from(GiveawayEntry).where(GiveawayEntry.giveaway_id == gw.id))).scalar() or 0
        if n < 1:
            await message.answer("Участников пока нет")
            return
        winners = await giveaway_svc.draw(s, notify.shop_bot, settings, gw)
    if not winners:
        await message.answer("Ни один участник не подписан на канал — некому вручать")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🎲 <b>Розыгрыш «{html.escape(gw.title)}» завершён!</b>", ""]
    for i, (uid, prize, code) in enumerate(winners):
        u = None
        async with SessionMaker() as s2:
            u = await s2.get(User, uid)
        name = f"@{u.username}" if u and u.username else f"id <code>{uid}</code>"
        extra = f" — купон <code>{code}</code>" if code else ""
        lines.append(f"{medals[i]} {name}: {prize}{extra}")
        # личка победителю через шоп-бот
        if notify.shop_bot is not None:
            try:
                dm = f"🎉 Ты выиграл в розыгрыше NORMWEAR: {medals[i]} {prize}!"
                if code:
                    dm += f"\nТвой личный купон (одноразовый): <code>{code}</code>"
                if i == 0:
                    dm += "\nНапиши нам размер и адрес — менеджер свяжется 🤝"
                await notify.shop_bot.send_message(uid, dm)
            except Exception:
                lines.append(f"  ⚠️ не смог написать {name} в личку — свяжись вручную")
    text = "\n".join(lines)
    # анонс в канал ответом на пост розыгрыша
    try:
        if notify.admin_bot is not None and gw.channel_message_id:
            await notify.admin_bot.send_message(settings.shop_channel_id, text, reply_to_message_id=gw.channel_message_id)
    except Exception:
        pass
    await message.answer(text)


@router.message(Command("droptimer"))
async def cmd_droptimer(message: Message):
    args = (message.text or "").split()[1:]
    # формат: /droptimer 60 "Новый дроп Corteiz"
    try:
        minutes = int(args[0]) if args else 60
    except ValueError:
        minutes = 60
    title = " ".join(args[1:]) if len(args) > 1 else "Новый дроп"
    title = title.strip('"').strip("'")[:64] or "Новый дроп"
    from datetime import timedelta

    drop_at = utcnow() + timedelta(minutes=minutes)
    async with SessionMaker() as s:
        from app.models import DropTimer

        timer = DropTimer(drop_at=drop_at, title=title)
        s.add(timer)
        await s.commit()
        await s.refresh(timer)
        tid = timer.id
    # постим в канал
    bot = notify.admin_bot
    if bot and settings.shop_channel_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Напомнить", url=f"https://t.me/{settings.shop_username}?start=remind_{tid}")]])
        try:
            msg = await bot.send_message(
                chat_id=settings.shop_channel_id,
                text=f"⏰ <b>{title}</b> через {minutes} мин!\n\nЖми 🔔 чтобы не пропустить — пришлём пуш в бот.",
            )
            # обновляем таймер с message_id
            async with SessionMaker() as s:
                t = await s.get(DropTimer, tid)
                if t:
                    t.channel_message_id = msg.message_id
                    await s.commit()
            await bot.send_message(settings.shop_channel_id, " ", reply_markup=kb)  # костыль чтобы кнопка была отдельно если нужно
            await message.answer(f"⏰ Таймер на {minutes} мин создан: <b>{title}</b> → канал, ID {tid}")
        except Exception as e:
            await message.answer(f"Ошибка поста в канал: {e}")
    else:
        await message.answer(f"Таймер создан {tid} на {minutes} мин, но канал не настроен")


@router.callback_query(F.data.startswith("drop_remind:"))
async def cb_drop_remind(cb: CallbackQuery):
    try:
        tid = int(cb.data.split(":")[1])
    except Exception:
        await cb.answer()
        return
    from app.models import DropReminder, DropTimer

    async with SessionMaker() as s:
        timer = await s.get(DropTimer, tid)
        if not timer or not timer.is_active:
            await cb.answer("Дроп уже прошёл", show_alert=True)
            return
        exists = (await s.scalars(select(DropReminder).where(DropReminder.user_id == cb.from_user.id, DropReminder.timer_id == tid))).first()
        if exists:
            await cb.answer("Уже напомним!", show_alert=True)
            return
        s.add(DropReminder(user_id=cb.from_user.id, timer_id=tid))
        await s.commit()
    await cb.answer("Напомню! 🔔", show_alert=True)
    try:
        await cb.message.answer("✅ Напомню о дропе — пришлю пуш когда выложим!")
    except Exception:
        pass


@router.message(F.forward_origin)
async def on_forward(message: Message):
    text = message.caption or message.text or ""
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    key = message.media_group_id or f"single:{message.chat.id}:{message.message_id}"
    data = _groups.setdefault(key, {"chat_id": message.chat.id, "texts": [], "photos": [], "task": None})
    if text:
        data["texts"].append(text)
    if file_id:
        data["photos"].append(file_id)
    if data["task"] is not None:
        data["task"].cancel()
    data["task"] = asyncio.create_task(_flush(key))


async def _flush(key: str) -> None:
    try:
        await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        return
    data = _groups.pop(key, None)
    if not data:
        return
    text = ""
    for t in reversed(data["texts"]):
        if t and t.strip():
            text = t
            break
    await process_supplier(data["chat_id"], text, data["photos"][:10])


async def process_supplier(chat_id: int, text: str, file_ids: list[str]) -> None:
    bot = notify.admin_bot
    if bot is None:
        return
    parsed = parse_product(text, known_brands=sorted(known_brands))
    if parsed is None:
        await bot.send_message(chat_id, "⚠️ Не удалось распознать товар (нужны цена и признаки позиции). Перешлите пост ещё раз или добавьте в подпись цену и размеры.")
        return
    async with SessionMaker() as s:
        brand = None
        if parsed.brand:
            slug = parsed.brand.lower().replace(" ", "_")[:64]
            brand = (await s.scalars(select(Brand).where(Brand.slug == slug))).first()
            if brand is None:
                brand = Brand(slug=slug, title=parsed.brand[:64])
                s.add(brand)
                await s.flush()
        category = None
        if parsed.category:
            cat_slug = parsed.category.lower().replace(" ", "_")[:64]
            category = (await s.scalars(select(Category).where(Category.slug == cat_slug))).first()
            if category is None:
                category = Category(slug=cat_slug, title=parsed.category[:64])
                s.add(category)
                await s.flush()
        product = Product(
            brand_id=brand.id if brand else None,
            category_id=category.id if category else None,
            title=parsed.title[:255],
            description=parsed.description,
            article=parsed.article,
            supplier_price=parsed.supplier_price,
            retail_price=retail_price(parsed.supplier_price, settings.default_margin_pct),
            sizes=parsed.sizes,
            stock=parsed.stock,
            status="pending",
            photo_mode="supplier" if file_ids else "yandex",
            supplier_channel=settings.supplier_channel_username,
            supplier_text=text[:4000],
        )
        s.add(product)
        await s.flush()
        for i, fid in enumerate(file_ids):
            s.add(ProductPhoto(product_id=product.id, source="supplier", url=fid, position=i))
        for i, path in enumerate(yandex_library.paths_for(parsed.brand, limit=6)):
            s.add(ProductPhoto(product_id=product.id, source="yandex", url=path, position=i))
        s.add(AdminAudit(admin_id=chat_id, action="parse_forward", entity="product", entity_id=product.id))
        await s.commit()
        pid = product.id
    await send_admin_product(chat_id, pid)


async def send_admin_product(chat_id: int, pid: int) -> None:
    bot = notify.admin_bot
    if bot is None:
        return
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None:
            return
        brand = await s.get(Brand, p.brand_id) if p.brand_id else None
        photos = (await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid).order_by(ProductPhoto.position))).all()
    sizes = ", ".join(str(x) for x in (p.sizes or [])[:14]) or "—"
    mode = "Яндекс.Диск" if p.photo_mode == "yandex" else "из поста"
    text = (
        f"🆔 <b>#{p.id}</b> · статус: {p.status}\n"
        f"<b>{html.escape(p.title)}</b>\n"
        f"🏷 Бренд: {html.escape(brand.title) if brand else '—'}\n"
        f"💰 Закупка: {int(p.supplier_price)} ₽ → Продажа: <b>{int(p.retail_price or 0)} ₽</b>\n"
        f"📏 Размеры: {html.escape(sizes)}\n"
        f"🖼 Фото: {sum(1 for ph in photos if ph.source == 'supplier')} из поста + {sum(1 for ph in photos if ph.source == 'yandex')} с диска (режим: {mode})"
    )
    kb_rows = [
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"ap:{p.id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rj:{p.id}")],
        [InlineKeyboardButton(text="✏️ Цена", callback_data=f"sp:{p.id}"), InlineKeyboardButton(text="🔄 Режим фото", callback_data=f"ph:{p.id}")],
    ]
    try:
        from app.services import vkposter as vkposter_svc

        if vkposter_svc.is_configured(settings):
            kb_rows.append([InlineKeyboardButton(text="📲 В ВК", callback_data=f"vk:{p.id}")])
    except Exception:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    from app.services.photos import resolve_local

    photo = next((ph.url for ph in photos if ph.source == "supplier"), None)
    local_photo = next((resolve_local(ph.url) for ph in photos if ph.source == "local"), None)
    try:
        if photo:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=kb)
        elif local_photo:
            from aiogram.types import FSInputFile

            await bot.send_photo(chat_id=chat_id, photo=FSInputFile(local_photo), caption=text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def process_supplier_auto(text: str, local_paths: list[str]) -> None:
    """Фаза 9: черновик из автопарсинга канала поставщика. Карточка — всем админам."""
    bot = notify.admin_bot
    if bot is None:
        return
    parsed = parse_product(text, known_brands=sorted(known_brands))
    if parsed is None:
        return
    async with SessionMaker() as s:
        # антидубль по тексту поставщика
        dup = (await s.scalars(select(Product).where(Product.supplier_text == text[:4000]))).first()
        if dup is not None:
            return
        brand = None
        if parsed.brand:
            slug = parsed.brand.lower().replace(" ", "_")[:64]
            brand = (await s.scalars(select(Brand).where(Brand.slug == slug))).first()
            if brand is None:
                brand = Brand(slug=slug, title=parsed.brand[:64])
                s.add(brand)
                await s.flush()
        category = None
        if parsed.category:
            cat_slug = parsed.category.lower().replace(" ", "_")[:64]
            category = (await s.scalars(select(Category).where(Category.slug == cat_slug))).first()
            if category is None:
                category = Category(slug=cat_slug, title=parsed.category[:64])
                s.add(category)
                await s.flush()
        product = Product(
            brand_id=brand.id if brand else None,
            category_id=category.id if category else None,
            title=parsed.title[:255],
            description=parsed.description,
            article=parsed.article,
            supplier_price=parsed.supplier_price,
            retail_price=retail_price(parsed.supplier_price, settings.default_margin_pct),
            sizes=parsed.sizes,
            stock=parsed.stock,
            status="pending",
            photo_mode="local" if local_paths else "yandex",
            supplier_channel=settings.supplier_channel_username,
            supplier_text=text[:4000],
        )
        s.add(product)
        await s.flush()
        for i, path in enumerate(local_paths[:10]):
            s.add(ProductPhoto(product_id=product.id, source="local", url=path, position=i))
        for i, path in enumerate(yandex_library.paths_for(parsed.brand, limit=6)):
            s.add(ProductPhoto(product_id=product.id, source="yandex", url=path, position=i + 10))
        s.add(AdminAudit(admin_id=0, action="parse_auto", entity="product", entity_id=product.id))
        await s.commit()
        pid = product.id
    for admin_id in settings.admin_ids:
        try:
            await send_admin_product(admin_id, pid)
        except Exception:
            continue


@router.callback_query(F.data.startswith("ap:"))
async def cb_approve(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    bot = notify.admin_bot
    if bot is None:
        await cb.answer("Бот не запущен", show_alert=True)
        return
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None:
            await cb.answer("Товар не найден", show_alert=True)
            return
        if p.status == "published":
            await cb.answer("Уже опубликован", show_alert=True)
            return
        p.retail_price = p.retail_price or retail_price(p.supplier_price, settings.default_margin_pct)
        try:
            await publish_product(bot, s, p, yandex_library, settings)
            p.status = "published"
            p.published_at = utcnow()
            s.add(AdminAudit(admin_id=cb.from_user.id, action="publish", entity="product", entity_id=pid))
            await s.commit()
            try:
                from app.services import retention as retention_svc

                await retention_svc.check_stock_requests(s, p)
            except Exception:
                pass
        except Exception as e:
            await s.rollback()
            await cb.answer(f"Ошибка публикации: {e}", show_alert=True)
            return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Опубликовано ✅")
    await cb.message.answer(f"✅ Товар #{pid} опубликован в @{settings.shop_channel_username}")
    # рассылка подписчикам дропов + бренда
    try:
        async with SessionMaker() as s2:
            p2 = await s2.get(Product, pid)
            title = p2.title if p2 else f"#{pid}"
            bid = p2.brand_id if p2 else None
        await notify.notify_drop_subscribers(title, pid, bid)
    except Exception:
        pass
    # напомнить тем кто жал Напомнить
    try:
        async with SessionMaker() as s3:
            from app.models import DropReminder, DropTimer

            timers = (await s3.scalars(select(DropTimer).where(DropTimer.is_active == True))).all()
            for timer in timers:
                rems = (await s3.scalars(select(DropReminder).where(DropReminder.timer_id == timer.id))).all()
                for r in rems:
                    try:
                        await notify.shop_bot.send_message(
                            r.user_id, f"🔥 Дроп <b>{timer.title}</b> уже тут! Новинка: {title} — смотри в канале @{settings.shop_channel_username}"
                        )
                    except Exception:
                        pass
                timer.is_active = False
            await s3.commit()
    except Exception:
        pass


@router.callback_query(F.data.startswith("rj:"))
async def cb_reject(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is not None:
            p.status = "rejected"
            s.add(AdminAudit(admin_id=cb.from_user.id, action="reject", entity="product", entity_id=pid))
            await s.commit()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Отклонён")


@router.callback_query(F.data.startswith("vk:"))
async def cb_vk_post(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    from app.services import vkposter as vkposter_svc

    if not vkposter_svc.is_configured(settings):
        await cb.answer("ВК не настроен", show_alert=True)
        return
    await cb.answer("Постю в ВК...")
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None:
            await cb.message.answer("Товар не найден")
            return
        brand = await s.get(Brand, p.brand_id) if p.brand_id else None
        photos = (await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid).order_by(ProductPhoto.position))).all()
        refs: list[tuple[str, str]] = []
        for ph in photos:
            if ph.source == "yandex":
                try:
                    href = await yandex_library.download_url(ph.url)
                    if href:
                        refs.append(("http", href))
                except Exception:
                    continue
            elif ph.source == "supplier" and isinstance(ph.url, str) and ph.url.startswith("http"):
                refs.append(("http", ph.url))
            elif ph.source == "local":
                from app.services.photos import resolve_local as _rl

                _real = _rl(ph.url)
                if _real:
                    refs.append(("file", _real))
            if len(refs) >= 4:
                break
        try:
            link = await vkposter_svc.post_product(settings, p, brand.title if brand else None, refs)
        except Exception as e:
            await cb.message.answer(f"⚠️ ВК не вышло: {e}")
            return
    if link:
        await cb.message.answer(f"📲 Опубликовано в ВК: {link}")
    else:
        await cb.message.answer("⚠️ ВК не вышло, проверь токен.")


@router.callback_query(F.data.startswith("rv_pub:"))
async def cb_review_publish(cb: CallbackQuery):
    try:
        rid = int(cb.data.split(":")[1])
    except Exception:
        await cb.answer()
        return
    import os

    from aiogram.types import FSInputFile

    from app.config import BASE_DIR
    from app.models import Review

    bot = notify.admin_bot
    async with SessionMaker() as s:
        rev = await s.get(Review, rid)
        if rev is None:
            await cb.answer("Отзыв не найден", show_alert=True)
            return
        if rev.is_published:
            await cb.answer("Уже опубликован", show_alert=True)
            return
        rev.is_published = True
        await s.commit()
        text, oid, uid = rev.text, rev.order_id, rev.user_id
    cap = f"⭐️ <b>Отзыв покупателя</b>\n\n{html.escape(text[:900])}\n\n🛒 Заказывай: @{settings.shop_username}"
    photo_path = str(BASE_DIR / "data" / "reviews" / f"rev_{oid}.jpg")
    try:
        if bot is not None and os.path.exists(photo_path):
            await bot.send_photo(chat_id=settings.shop_channel_id, photo=FSInputFile(photo_path), caption=cap)
        elif bot is not None:
            await bot.send_message(chat_id=settings.shop_channel_id, text=cap)
    except Exception as e:
        await cb.answer(f"Не вышло: {e}", show_alert=True)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Опубликован ✅")
    await notify.notify_user(uid, "⭐️ Твой отзыв опубликован в канале! Спасибо!")


@router.callback_query(F.data.startswith("rv_del:"))
async def cb_review_delete(cb: CallbackQuery):
    try:
        rid = int(cb.data.split(":")[1])
    except Exception:
        await cb.answer()
        return
    from app.models import LoyaltyTransaction, Review

    async with SessionMaker() as s:
        rev = await s.get(Review, rid)
        if rev is None:
            await cb.answer("Отзыв не найден", show_alert=True)
            return
        user = await s.get(User, rev.user_id)
        if user:
            user.bonus_points = max(0, (user.bonus_points or 0) - 200)
            s.add(LoyaltyTransaction(user_id=user.id, order_id=rev.order_id, points=-200, kind="review", note=f"Отзыв №{rid} отклонён"))
        await s.delete(rev)
        await s.commit()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Отклонён")


@router.callback_query(F.data.startswith("sp:"))
async def cb_price(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[1])
    await state.set_state(AdminFSM.price)
    await state.update_data(product_id=pid)
    await cb.message.answer(f"✏️ Новая цена продажи для товара #{pid} (₽):")
    await cb.answer()


@router.message(AdminFSM.price, F.text)
async def st_price(message: Message, state: FSMContext):
    d = await state.get_data()
    pid = int(d.get("product_id") or 0)
    await state.clear()
    raw = (message.text or "").replace(" ", "").replace("₽", "").replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        await message.answer("Не похоже на число. Нажмите «✏️ Цена» ещё раз.")
        return
    if val <= 0:
        await message.answer("Цена должна быть больше нуля.")
        return
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is not None:
            old_price = int(p.retail_price or 0)
            p.retail_price = round(val)
            s.add(AdminAudit(admin_id=message.from_user.id, action="set_price", entity="product", entity_id=pid, payload={"price": round(val)}))
            await s.commit()
            try:
                from app.services import retention as retention_svc

                n = await retention_svc.check_stock_requests(s, p)
                if n:
                    await message.answer(f"📩 Размер дождались {n} чел. — пуш отправлен.")
            except Exception:
                pass
            # цена упала — пуш избранному
            try:
                if old_price and round(val) < old_price:
                    from app.models import Favorite

                    from aiogram.types import InlineKeyboardButton as _B
                    from aiogram.types import InlineKeyboardMarkup as _KB

                    favs = (await s.scalars(select(Favorite).where(Favorite.product_id == pid))).all()
                    kb = _KB(inline_keyboard=[[_B(text="🛍 Открыть", callback_data=f"pr:{pid}")]])
                    cnt = 0
                    for f in favs:
                        try:
                            if notify.shop_bot is not None:
                                await notify.shop_bot.send_message(
                                    f.user_id,
                                    f"📉 Цена упала: <b>{html.escape(p.title)}</b> — было {old_price}₽, стало {int(round(val))}₽!",
                                    reply_markup=kb,
                                )
                                cnt += 1
                        except Exception:
                            continue
                    if cnt:
                        await message.answer(f"📉 Об уценке сообщил {cnt} чел. из избранного.")
            except Exception:
                pass
    await message.answer(f"💰 Цена товара #{pid}: {round(val)} ₽")
    await send_admin_product(message.chat.id, pid)


@router.callback_query(F.data.startswith("ph:"))
async def cb_photo_mode(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None:
            await cb.answer("Товар не найден", show_alert=True)
            return
        p.photo_mode = "supplier" if p.photo_mode == "yandex" else "yandex"
        await s.commit()
        mode = p.photo_mode
    await send_admin_product(cb.from_user.id, pid)
    await cb.answer(f"Режим фото: {'Яндекс.Диск' if mode == 'yandex' else 'из поста'}")


@router.callback_query(F.data.startswith("tk:"))
async def cb_ticket(cb: CallbackQuery, state: FSMContext):
    uid = int(cb.data.split(":")[1])
    pending_reply[cb.from_user.id] = uid
    await state.clear()
    await cb.message.answer(f"💬 Режим ответа клиенту <code>{uid}</code>.\nНапишите сообщение — оно уйдёт клиенту.\n/admin — выйти из режима.")
    await cb.answer()


@router.message(F.text & ~F.command)
async def admin_reply(message: Message):
    uid = pending_reply.get(message.from_user.id if message.from_user else 0)
    if uid is None:
        await message.answer("Не понял 🤔 Перешлите пост поставщика, /admin — панель, /orders — заказы.")
        return
    text = (message.text or "").strip()
    if not text:
        return
    async with SessionMaker() as s:
        ticket = (
            await s.scalars(
                select(SupportTicket)
                .where(SupportTicket.user_id == uid, SupportTicket.status == "open")
                .order_by(SupportTicket.id.desc())
            )
        ).first()
        if ticket is None:
            ticket = SupportTicket(user_id=uid)
            s.add(ticket)
            await s.flush()
        s.add(SupportMessage(ticket_id=ticket.id, from_admin=True, text=text[:3000]))
        ticket.updated_at = utcnow()
        await s.commit()
    await notify.notify_user(uid, f"💬 <b>Поддержка NORMWEAR</b>\n\n{html.escape(text[:3000])}")
    await message.answer("✅ Отправлено клиенту")