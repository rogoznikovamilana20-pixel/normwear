import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionMaker
from app.models import Brand, CartItem, Order, Product, ProductPhoto, SupportMessage, SupportTicket, User
from app.services import notify, orders
from app.services.photos import yandex_library

router = Router()
settings = get_settings()

MENU = {"🛍 Каталог", "🛒 Корзина", "📦 Мои заказы", "🎁 Бонусы", "📞 Поддержка", "🔔 Дропы"}

WELCOME = (
    "👋 Добро пожаловать в <b>NORMWEAR</b>\n"
    "\n"
    "Стритвир и кроссовки топовых брендов.\n"
    "💰 Честные цены · 🚚 Доставка 2–4 дня · ✅ Проверка перед отправкой\n"
    "\n"
    "Выбирай раздел внизу и собирай корзину. Бонусы начисляются с каждого заказа."
)


def _miniapp_url() -> str | None:
    # берём из .env MINIAPP_URL_TEMPLATE, иначе пробуем duckdns, иначе локально
    tpl = settings.miniapp_url_template
    if tpl and tpl.strip():
        # если шаблон с {product_id} — убираем параметр для главной
        return tpl.split("?")[0].replace("/app/", "/").rstrip("/") + "/"
    # fallback — локально (в ТГ нужен https, но для браузера сойдёт)
    return None


def main_menu() -> ReplyKeyboardMarkup:
    miniapp = _miniapp_url()
    rows = [
        [KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="🛒 Корзина")],
        [KeyboardButton(text="📦 Мои заказы"), KeyboardButton(text="🎁 Бонусы")],
        [KeyboardButton(text="🔔 Дропы"), KeyboardButton(text="📞 Поддержка")],
    ]
    # WebApp кнопка видна только в ТГ, требует https — добавляем только если url https
    if miniapp and miniapp.startswith("https://"):
        try:
            rows.insert(0, [KeyboardButton(text="✨ Мини-каталог", web_app=WebAppInfo(url=miniapp))])
        except Exception:
            pass
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])


class Checkout(StatesGroup):
    name = State()
    phone = State()
    city = State()
    address = State()
    promo = State()
    confirm = State()


class ReviewFSM(StatesGroup):
    text = State()


pending_review: dict[int, int] = {}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    payload = message.text.split(" ", 1)[1].strip() if " " in (message.text or "") else ""
    referrer_id = None
    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            referrer_id = None
    async with SessionMaker() as s:
        await orders.get_or_create_user(s, message.from_user, referrer_id=referrer_id)
        await s.commit()
    if payload == "checkout":
        await render_cart(message, message.from_user.id)
        return
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(StateFilter(None), F.text.in_(MENU))
async def menu_texts(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text == "🛍 Каталог":
        await send_brands(message)
    elif text == "🛒 Корзина":
        await render_cart(message, message.from_user.id)
    elif text == "📦 Мои заказы":
        await send_my_orders(message)
    elif text == "🎁 Бонусы":
        await send_bonuses(message)
    elif text == "📞 Поддержка":
        await message.answer("✍️ Напишите ваш вопрос одним сообщением — менеджер ответит прямо в этот чат.")
    elif text == "🔔 Дропы":
        await send_drops(message)


@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Главное меню", reply_markup=main_menu())
    await cb.answer()


@router.callback_query(F.data == "cat")
async def cb_cat(cb: CallbackQuery):
    await send_brands(cb.message)
    await cb.answer()


@router.callback_query(F.data == "cart")
async def cb_cart(cb: CallbackQuery):
    await render_cart(cb.message, cb.from_user.id)
    await cb.answer()


async def send_brands(target: Message):
    async with SessionMaker() as s:
        rows = (
            await s.execute(
                select(Brand.id, Brand.title)
                .join(Product, Product.brand_id == Brand.id)
                .where(Product.status == "published")
                .distinct()
                .order_by(Brand.title)
            )
        ).all()
    if not rows:
        await target.answer("Каталог пока пуст — новые дропы скоро 🙂", reply_markup=main_menu())
        return
    buttons = [InlineKeyboardButton(text=title, callback_data=f"br:{bid}") for bid, title in rows]
    rows_kb = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows_kb.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    await target.answer("🛍 <b>Каталог</b>\nВыберите бренд:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))


@router.callback_query(F.data.startswith("br:"))
async def cb_brand(cb: CallbackQuery):
    parts = cb.data.split(":")
    bid = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    async with SessionMaker() as s:
        brand = await s.get(Brand, bid)
        if brand is None:
            await cb.answer("Бренд не найден", show_alert=True)
            return
        prods = (
            await s.scalars(
                select(Product)
                .where(Product.brand_id == bid, Product.status == "published")
                .order_by(Product.id.desc())
                .limit(6)
                .offset(page * 6)
            )
        ).all()
        total = (
            await session_count_published(s, bid)
        )
    if not prods and page == 0:
        await cb.answer("У этого бренда пока нет товаров", show_alert=True)
        return
    rows = []
    for p in prods:
        rows.append([InlineKeyboardButton(text=f"{p.title[:34]} · {int(p.retail_price or 0)} ₽", callback_data=f"pr:{p.id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"br:{bid}:{page - 1}"))
    if (page + 1) * 6 < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"br:{bid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Каталог", callback_data="cat"), InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")])
    try:
        await cb.message.edit_text(f"🏷 <b>{html.escape(brand.title)}</b> · стр. {page + 1}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        try:
            await cb.message.answer(f"🏷 <b>{html.escape(brand.title)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception:
            pass
    try:
        await cb.answer()
    except Exception:
        pass


async def session_count_published(s, bid: int) -> int:
    return (await s.execute(select(func.count()).select_from(Product).where(Product.brand_id == bid, Product.status == "published"))).scalar() or 0


@router.callback_query(F.data.startswith("pr:"))
async def cb_product(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None or p.status != "published":
            await cb.answer("Товар недоступен", show_alert=True)
            return
        brand = await s.get(Brand, p.brand_id) if p.brand_id else None
        photos = (
            await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid).order_by(ProductPhoto.position))
        ).all()
    sizes = [str(x) for x in (p.sizes or [])]
    text = f"<b>{html.escape(p.title)}</b>"
    if brand is not None:
        text = f"<b>{html.escape(brand.title)}</b> · " + text
    text += f"\n\n💰 Цена: <b>{int(p.retail_price or 0)} ₽</b>"
    if sizes:
        text += f"\n📏 Размеры: {html.escape(', '.join(sizes))}"
    if sizes:
        text += "\n\nВыберите размер:"
    rows = []
    for i in range(0, len(sizes), 4):
        rows.append([InlineKeyboardButton(text=sizes[j], callback_data=f"add:{pid}:{j}") for j in range(i, min(i + 4, len(sizes)))])
    rows.append([InlineKeyboardButton(text="🏠 Каталог", callback_data="cat"), InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    # file_id из форварда валиден только для admin-бота, shop-бот должен использовать только Yandex href
    photo = None
    for ph in photos:
        if ph.source == "yandex":
            try:
                href = await yandex_library.download_url(ph.url)
                if href:
                    photo = href
                    break
            except Exception:
                continue
    # fallback: supplier URL только если это http (не file_id)
    if photo is None:
        for ph in photos:
            if ph.source == "supplier" and isinstance(ph.url, str) and ph.url.startswith("http"):
                photo = ph.url
                break
    try:
        if photo:
            await cb.message.answer_photo(photo=photo, caption=text, reply_markup=kb)
        else:
            await cb.message.answer(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("add:"))
async def cb_add(cb: CallbackQuery):
    parts = cb.data.split(":")
    pid = int(parts[1])
    idx = int(parts[2]) if len(parts) > 2 else 0
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None or p.status != "published":
            await cb.answer("Товар недоступен", show_alert=True)
            return
        sizes = [str(x) for x in (p.sizes or [])]
        size = sizes[idx] if idx < len(sizes) else ""
        existing = (
            await s.scalars(
                select(CartItem).where(
                    CartItem.user_id == cb.from_user.id,
                    CartItem.product_id == pid,
                    CartItem.size == size,
                )
            )
        ).first()
        if existing is None:
            s.add(CartItem(user_id=cb.from_user.id, product_id=pid, size=size))
            await s.commit()
    await cb.answer("✅ Добавлено в корзину")


@router.callback_query(F.data.startswith("ci:"))
async def cb_cart_item(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 3 or parts[2] != "del":
        await cb.answer()
        return
    ci_id = int(parts[1])
    async with SessionMaker() as s:
        ci = await s.get(CartItem, ci_id)
        if ci is not None and ci.user_id == cb.from_user.id:
            await s.delete(ci)
            await s.commit()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await render_cart(cb.message, cb.from_user.id)
    await cb.answer("Удалено")


async def render_cart(target: Message, user_id: int):
    items = await run_get_cart(user_id)
    if not items:
        await target.answer("🛒 Корзина пуста", reply_markup=main_menu())
        return
    lines = ["<b>🛒 Ваша корзина</b>", ""]
    subtotal = 0
    rows = []
    for ci, p in items:
        price = int(p.retail_price or 0)
        subtotal += price
        label = p.title[:28] + (f" · {ci.size}" if ci.size else "")
        lines.append(f"• {html.escape(label)} — {price} ₽")
        rows.append([InlineKeyboardButton(text=f"🗑 {label}", callback_data=f"ci:{ci.id}:del")])
    lines += ["", f"<b>Итого: {subtotal} ₽</b>", "", "Оплата после подтверждения заказа менеджером."]
    rows.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="chk")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    await target.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def run_get_cart(user_id: int):
    async with SessionMaker() as s:
        return await orders.get_cart(s, user_id)


@router.callback_query(StateFilter(None), F.data == "chk")
async def cb_checkout(cb: CallbackQuery, state: FSMContext):
    async with SessionMaker() as s:
        cnt = await orders.cart_count(s, cb.from_user.id)
    if cnt == 0:
        await cb.answer("Корзина пуста", show_alert=True)
        return
    await state.set_state(Checkout.name)
    await cb.message.answer("📦 <b>Оформление заказа</b>\n\nКак вас зовут? (имя для доставки)", reply_markup=cancel_kb())
    await cb.answer()


@router.message(Checkout.name, F.text)
async def st_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите имя (минимум 2 символа):")
        return
    await state.update_data(full_name=name[:120])
    await state.set_state(Checkout.phone)
    await message.answer("📱 Номер телефона для связи:")


@router.message(Checkout.phone, F.text)
async def st_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if sum(ch.isdigit() for ch in phone) < 10:
        await message.answer("Похоже на неверный номер. Введите телефон в формате +7XXXXXXXXXX:")
        return
    await state.update_data(phone=phone[:30])
    await state.set_state(Checkout.city)
    await message.answer("🏙 Город:")


@router.message(Checkout.city, F.text)
async def st_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("Введите город:")
        return
    await state.update_data(city=city[:60])
    await state.set_state(Checkout.address)
    await message.answer("📍 Адрес: улица, дом, квартира:")


@router.message(Checkout.address, F.text)
async def st_address(message: Message, state: FSMContext):
    address = (message.text or "").strip()
    if len(address) < 5:
        await message.answer("Введите адрес полностью:")
        return
    await state.update_data(address=address[:250])
    await show_summary(message, state)


@router.callback_query(Checkout.confirm, F.data == "cfp")
async def cb_promo_input(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Checkout.promo)
    await cb.message.answer("🎟 Введите промокод (или «-», чтобы убрать):", reply_markup=cancel_kb())
    await cb.answer()


@router.message(Checkout.promo, F.text)
async def st_promo(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    await state.update_data(promo_code=None if code == "-" else code[:32])
    await show_summary(message, state)


@router.callback_query(Checkout.confirm, F.data == "cfb")
async def cb_bonus_toggle(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.update_data(use_bonus=not d.get("use_bonus", False))
    await show_summary(cb.message, state)
    await cb.answer()


async def show_summary(target: Message, state: FSMContext):
    user_id = state.key.user_id
    d = await state.get_data()
    async with SessionMaker() as s:
        user = await s.get(User, user_id)
        totals = await orders.preview_totals(s, user, d.get("promo_code"), bool(d.get("use_bonus")))
    lines = [
        "<b>📋 Проверьте заказ</b>",
        "",
        f"👤 {html.escape(d.get('full_name', ''))}",
        f"📱 {html.escape(d.get('phone', ''))}",
        f"🏙 {html.escape(d.get('city', ''))}, {html.escape(d.get('address', ''))}",
        "",
        f"Товары: {int(totals['subtotal'])} ₽",
    ]
    if totals["discount"]:
        lines.append(f"🎟 Промокод {d.get('promo_code')}: −{int(totals['discount'])} ₽")
    if totals["bonus_used"]:
        lines.append(f"🎁 Бонусы: −{totals['bonus_used']} ₽")
    if totals.get("promo_error"):
        lines.append(f"⚠️ {totals['promo_error']}")
    lines += [f"<b>Итого: {int(totals['total'])} ₽</b>", f"🎁 Будет начислено бонусов: +{totals['bonus_earned']}"]
    rows = [[InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="cf")]]
    row2 = [InlineKeyboardButton(text=("🎟 Сменить промокод" if d.get("promo_code") else "🎟 Промокод"), callback_data="cfp")]
    if totals["bonus_max"] > 0:
        row2.append(
            InlineKeyboardButton(
                text=("🎁 Не списывать бонусы" if d.get("use_bonus") else f"🎁 Списать {totals['bonus_max']} бонусов"),
                callback_data="cfb",
            )
        )
    rows.append(row2)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    await state.set_state(Checkout.confirm)
    await target.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(Checkout.confirm, F.data == "cf")
async def cb_confirm(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    user_id = state.key.user_id
    await state.clear()
    async with SessionMaker() as s:
        user = await s.get(User, user_id)
        order, err = await orders.create_order(s, user, d)
        username = user.username if user else None
    if order is None:
        await cb.message.answer(f"⚠️ {err}", reply_markup=main_menu())
        await cb.answer()
        return
    # Инструкция для ручной оплаты (СБП/перевод) — оптимально для старта без комиссий
    pay_hint = (
        "💳 <b>Как оплатить:</b> менеджер пришлёт реквизиты СБП/карты в этот чат в течение 15 минут.\n"
        "После оплаты пришлите чек — заказ сразу отправим."
        if (getattr(settings, "sbp_provider", None) or "manual") == "manual"
        else "💳 Ссылка на оплату придёт от менеджера."
    )
    await cb.message.answer(
        f"✅ <b>Заказ №{order.id} оформлен!</b>\n\n"
        f"Сумма: <b>{int(order.total)} ₽</b>\n"
        f"👤 {html.escape(order.full_name)} · {html.escape(order.phone)}\n\n"
        f"{pay_hint}\n\n"
        f"Статус отслеживайте в «📦 Мои заказы».",
        reply_markup=main_menu(),
    )
    await cb.answer()
    items_lines = []
    async with SessionMaker() as s:
        from app.models import OrderItem

        items = (await s.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    for it in items:
        items_lines.append(f"• {html.escape(it.title)}{(' · ' + it.size) if it.size else ''} — {int(it.price)} ₽")
    uname = f"@{username}" if username else "без юзернейма"
    admin_text = (
        f"🆕 <b>Новый заказ №{order.id}</b>\n"
        f"👤 {html.escape(order.full_name)} · {uname} · id <code>{order.user_id}</code>\n"
        f"📱 {html.escape(order.phone)}\n"
        f"🏙 {html.escape(order.city)}, {html.escape(order.address)}\n\n"
        + "\n".join(items_lines)
        + f"\n\n💰 <b>Итого: {int(order.total)} ₽</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Открыть заказ", callback_data=f"od:{order.id}")],
            [InlineKeyboardButton(text="➡ Готов к оплате", callback_data=f"od:{order.id}:next")],
        ]
    )
    await notify.notify_admins(admin_text, kb)


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Заказ отменён. Главное меню:", reply_markup=main_menu())
    await cb.answer()


async def send_my_orders(message: Message):
    async with SessionMaker() as s:
        ords = (
            await s.scalars(
                select(Order).where(Order.user_id == message.from_user.id).order_by(Order.id.desc()).limit(8)
            )
        ).all()
    if not ords:
        await message.answer("Заказов пока нет — загляните в «🛍 Каталог» 😉", reply_markup=main_menu())
        return
    lines = ["<b>📦 Ваши заказы</b>", ""]
    for o in ords:
        label = notify.ORDER_STATUS_LABEL.get(o.status, o.status)
        lines.append(f"№{o.id} · {label} · {int(o.total)} ₽ · {o.created_at:%d.%m.%Y}")
    lines += ["", "Статусы обновляются автоматически."]
    await message.answer("\n".join(lines), reply_markup=main_menu())


async def send_bonuses(message: Message):
    async with SessionMaker() as s:
        user = await s.get(User, message.from_user.id)
    points = user.bonus_points if user else 0
    link = f"https://t.me/{settings.shop_username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"🎁 <b>Бонусная программа</b>\n\n"
        f"Ваш баланс: <b>{points} бонусов</b>\n\n"
        f"• 1% от покупки возвращается бонусами (1 бонус = 1 ₽)\n"
        f"• Бонусами можно оплатить до 30% заказа\n\n"
        f"👥 <b>Приведи друга</b>\n"
        f"Отправь другу ссылку:\n{link}\n"
        f"Друг сделает первый заказ — получишь <b>+100 бонусов</b>",
        reply_markup=main_menu(),
    )


async def send_drops(message: Message):
    from app.models import DropSubscription

    async with SessionMaker() as s:
        sub = await s.get(DropSubscription, message.from_user.id)
    is_sub = sub is not None
    text = (
        "🔔 <b>Подписка на дропы</b>\n\n"
        "Получай первым уведомления о новых товарах.\n"
        f"Статус: {'✅ Подписан' if is_sub else '❌ Не подписан'}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=("❌ Отписаться" if is_sub else "✅ Подписаться"), callback_data="drop_sub")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
        ]
    )
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "drop_sub")
async def cb_drop_sub(cb: CallbackQuery):
    from app.models import DropSubscription

    async with SessionMaker() as s:
        sub = await s.get(DropSubscription, cb.from_user.id)
        if sub is None:
            s.add(DropSubscription(user_id=cb.from_user.id))
            await s.commit()
            await cb.answer("Подписался ✅")
            await cb.message.answer("🔔 Ты подписан на дропы — пришлём новинки первым!")
        else:
            await s.delete(sub)
            await s.commit()
            await cb.answer("Отписался")
            await cb.message.answer("🔕 Отписался от дропов.")


@router.callback_query(F.data.startswith("rev:"))
async def cb_rev_start(cb: CallbackQuery, state: FSMContext):
    try:
        oid = int(cb.data.split(":")[1])
    except Exception:
        await cb.answer()
        return
    pending_review[cb.from_user.id] = oid
    await state.set_state(ReviewFSM.text)
    await cb.message.answer("⭐️ Напиши отзыв одним сообщением — текст + можно приложить фото позже. За отзыв +50 бонусов!")
    await cb.answer()


@router.callback_query(F.data == "rev_skip")
async def cb_rev_skip(cb: CallbackQuery):
    await cb.answer("Ок, в другой раз!")
    try:
        await cb.message.delete()
    except Exception:
        pass


@router.message(ReviewFSM.text, F.text)
async def st_review_text(message: Message, state: FSMContext):
    oid = pending_review.get(message.from_user.id)
    if oid is None:
        await state.clear()
        return
    text = (message.text or "").strip()[:2000]
    if len(text) < 5:
        await message.answer("Напиши чуть подробнее (минимум 5 символов):")
        return
    async with SessionMaker() as s:
        from app.models import Review, User as U, LoyaltyTransaction

        order = await s.get(Order, oid)
        if order is None or order.user_id != message.from_user.id:
            await message.answer("Заказ не найден.")
            await state.clear()
            pending_review.pop(message.from_user.id, None)
            return
        if order.status != "completed":
            await message.answer("Отзыв можно оставить только после завершения заказа.")
            await state.clear()
            pending_review.pop(message.from_user.id, None)
            return
        # не даём дважды за один заказ
        exists = (await s.scalars(select(Review).where(Review.order_id == oid))).first()
        if exists:
            await message.answer("Ты уже оставил отзыв по этому заказу.")
            await state.clear()
            pending_review.pop(message.from_user.id, None)
            return
        s.add(Review(user_id=message.from_user.id, order_id=oid, text=text, rating=5))
        user = await s.get(U, message.from_user.id)
        if user:
            user.bonus_points = (user.bonus_points or 0) + 50
            s.add(LoyaltyTransaction(user_id=user.id, order_id=oid, points=50, kind="review", note=f"Бонус за отзыв к заказу №{oid}"))
        await s.commit()
    await state.clear()
    pending_review.pop(message.from_user.id, None)
    await message.answer("Спасибо за отзыв! +50 бонусов начислено 🎁", reply_markup=main_menu())
    # уведомить админов
    uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    await notify.notify_admins(f"⭐️ <b>Новый отзыв</b> к заказу №{oid}\n👤 {html.escape(message.from_user.first_name or '')} {uname} · <code>{message.from_user.id}</code>\n\n{html.escape(text[:1000])}")


@router.message(StateFilter(None), F.text & ~F.command)
async def support_message(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in MENU:
        return
    if message.from_user.is_bot:
        return
    async with SessionMaker() as s:
        ticket = (
            await s.scalars(
                select(SupportTicket)
                .where(SupportTicket.user_id == message.from_user.id, SupportTicket.status == "open")
                .order_by(SupportTicket.id.desc())
            )
        ).first()
        if ticket is None:
            ticket = SupportTicket(user_id=message.from_user.id)
            s.add(ticket)
            await s.flush()
        s.add(SupportMessage(ticket_id=ticket.id, from_admin=False, text=text[:3000]))
        ticket.updated_at = __import__("app.models", fromlist=["utcnow"]).utcnow()
        await s.commit()
        ticket_id = ticket.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"tk:{message.from_user.id}")]])
    uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    await notify.notify_admins(
        f"💬 <b>Сообщение от клиента</b>\n"
        f"👤 {html.escape(message.from_user.first_name or '')} {uname} · id <code>{message.from_user.id}</code> · тикет #{ticket_id}\n\n"
        f"{html.escape(text[:1000])}",
        kb,
    )
    await message.answer("Сообщение отправлено менеджеру ✅ Ответ придёт в этот чат.")