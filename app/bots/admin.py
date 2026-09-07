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


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Заказы", callback_data="orders"), InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="🗂 На модерации", callback_data="pending")],
        ]
    )
    await message.answer(
        "🛠 <b>Админ-панель NORMWEAR</b>\n\n"
        f"📩 Просто перешлите пост из @{settings.supplier_channel_username} в этот чат — бот распарсит товар и покажет карточку.\n\n"
        "Команды: /orders — заказы · /promo CODE PERCENT [MIN] [MAXUSES] — промокод",
        reply_markup=kb,
    )


@router.message(Command("orders"))
async def cmd_orders(message: Message):
    await send_orders_list(message)


@router.callback_query(F.data == "orders")
async def cb_orders(cb: CallbackQuery):
    await send_orders_list(cb.message)
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
    if action == "next":
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
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Напомнить", callback_data=f"drop_remind:{tid}")]])
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
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"ap:{p.id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rj:{p.id}")],
            [InlineKeyboardButton(text="✏️ Цена", callback_data=f"sp:{p.id}"), InlineKeyboardButton(text="🔄 Режим фото", callback_data=f"ph:{p.id}")],
        ]
    )
    photo = next((ph.url for ph in photos if ph.source == "supplier"), None)
    try:
        if photo:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


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
            p.retail_price = round(val)
            s.add(AdminAudit(admin_id=message.from_user.id, action="set_price", entity="product", entity_id=pid, payload={"price": round(val)}))
            await s.commit()
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