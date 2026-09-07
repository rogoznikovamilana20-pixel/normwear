import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionMaker
from app.models import Brand, CartItem, FortuneSpin, Order, Product, ProductPhoto, PromoCode, SupportMessage, SupportTicket, User
from app.services import notify, orders
from app.services.photos import yandex_library

router = Router()
settings = get_settings()

MENU = {"🛍 Каталог", "🛒 Корзина", "☰ Меню", "📦 Мои заказы", "🎁 Бонусы", "📞 Поддержка", "🔔 Дропы", "🎡 Колесо", "📦 BOX"}

WELCOME = (
    "👋 Добро пожаловать в <b>NORMWEAR</b>\n"
    "\n"
    "Стритвир и кроссовки топовых брендов.\n"
    "💰 Честные цены · 🚚 Доставка 2–4 дня · ✅ Проверка перед отправкой\n"
    "\n"
    "Выбирай раздел внизу и собирай корзину. Бонусы начисляются с каждого заказа."
)


def _miniapp_url() -> str | None:
    # Только публичный https подходит для Telegram WebApp.
    # Шаблон вида https://host/app/?product={id} -> корень https://host/
    tpl = (settings.miniapp_url_template or "").strip()
    if tpl:
        base = tpl.split("?")[0].replace("/app/", "/").rstrip("/") + "/"
        if base.startswith("https://"):
            return base
    # canonical fallback — Render (когда поднимется, кнопки сразу оживут)
    return "https://normwear-shop.onrender.com/"


def miniapp_available() -> bool:
    # Проверять жив ли хост здесь не будем — ТГ сам покажет ошибку.
    # Кнопку показываем всегда, чтобы мини-апп был виден в боте/канале.
    return True


def main_menu() -> ReplyKeyboardMarkup:
    # компактно: только 3 кнопки внизу, остальное — inline в чате
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="🛒 Корзина")],
            [KeyboardButton(text="☰ Меню")],
        ],
        resize_keyboard=True,
    )


def menu_inline() -> InlineKeyboardMarkup:
    miniapp = _miniapp_url()
    rows: list[list[InlineKeyboardButton]] = []
    # Мини-каталог всегда первой строкой: WebApp если https, иначе ссылка на бота
    try:
        from aiogram.types import WebAppInfo as _WAI

        if miniapp and miniapp.startswith("https://"):
            rows.append([InlineKeyboardButton(text="✨ Мини-каталог", web_app=_WAI(url=miniapp))])
        else:
            rows.append([InlineKeyboardButton(text="✨ Мини-каталог", url=f"https://t.me/{settings.shop_username}?start=catalog")])
    except Exception:
        pass
    rows += [
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="m_orders"), InlineKeyboardButton(text="🎁 Бонусы", callback_data="m_bonus")],
        [InlineKeyboardButton(text="🔔 Дропы", callback_data="m_drops"), InlineKeyboardButton(text="🎡 Колесо", callback_data="m_wheel")],
        [InlineKeyboardButton(text="📦 BOX", callback_data="m_box"), InlineKeyboardButton(text="📞 Поддержка", callback_data="m_support")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_menu(target: Message):
    await target.answer("☰ <b>Меню NORMWEAR</b>\nВыбери раздел:", reply_markup=menu_inline())


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
    if payload == "catalog":
        await message.answer(WELCOME, reply_markup=main_menu())
        await send_brands(message)
        return
    if payload == "wheel":
        await message.answer(WELCOME, reply_markup=main_menu())
        await send_wheel(message)
        return
    if payload.startswith("product_"):
        await message.answer(WELCOME, reply_markup=main_menu())
        try:
            pid = int(payload.split("_", 1)[1])
            async with SessionMaker() as s:
                p = await s.get(Product, pid)
                if p is not None and p.status == "published":
                    brand = await s.get(Brand, p.brand_id) if p.brand_id else None
                    bname = f"<b>{html.escape(brand.title)}</b> · " if brand else ""
                    await message.answer(
                        f"{bname}<b>{html.escape(p.title)}</b>\n\n💰 <b>{int(p.retail_price or 0)}₽</b>\n📏 {html.escape(', '.join(str(x) for x in (p.sizes or [])) or '—')}",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛍 Открыть карточку", callback_data=f"pr:{p.id}")],
                            [InlineKeyboardButton(text="🛍 Весь каталог", callback_data="cat")],
                        ]),
                    )
                    return
        except Exception:
            pass
        await send_brands(message)
        return
    await message.answer(WELCOME, reply_markup=main_menu())
    await send_menu(message)


@router.message(StateFilter(None), F.text.in_(MENU))
async def menu_texts(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text == "🛍 Каталог":
        await send_brands(message)
    elif text == "🛒 Корзина":
        await render_cart(message, message.from_user.id)
    elif text == "☰ Меню":
        await send_menu(message)
    elif text == "📦 Мои заказы":
        await send_my_orders(message)
    elif text == "🎁 Бонусы":
        await send_bonuses(message)
    elif text == "📞 Поддержка":
        await message.answer("✍️ Напишите ваш вопрос одним сообщением — менеджер ответит прямо в этот чат.")
    elif text == "🔔 Дропы":
        await send_drops(message)
    elif text == "🎡 Колесо":
        await send_wheel(message)
    elif text == "📦 BOX":
        await send_box(message)


@router.callback_query(F.data == "m_menu")
async def cb_m_menu(cb: CallbackQuery):
    await cb.message.answer("☰ <b>Меню NORMWEAR</b>\nВыбери раздел:", reply_markup=menu_inline())
    await cb.answer()


@router.callback_query(F.data == "m_orders")
async def cb_m_orders(cb: CallbackQuery):
    await send_my_orders(cb.message)
    await cb.answer()


@router.callback_query(F.data == "m_bonus")
async def cb_m_bonus(cb: CallbackQuery):
    await send_bonuses(cb.message)
    await cb.answer()


@router.callback_query(F.data == "m_drops")
async def cb_m_drops(cb: CallbackQuery):
    await send_drops(cb.message)
    await cb.answer()


@router.callback_query(F.data == "m_wheel")
async def cb_m_wheel(cb: CallbackQuery):
    await send_wheel(cb.message)
    await cb.answer()


@router.callback_query(F.data == "m_box")
async def cb_m_box(cb: CallbackQuery):
    await send_box(cb.message)
    await cb.answer()


@router.callback_query(F.data == "m_support")
async def cb_m_support(cb: CallbackQuery):
    await cb.message.answer("✍️ Напишите ваш вопрос одним сообщением — менеджер ответит прямо в этот чат.")
    await cb.answer()


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
    # подписка на бренд
    from app.models import BrandSubscription

    async with SessionMaker() as s2:
        is_sub = (
            await s2.scalars(select(BrandSubscription).where(BrandSubscription.user_id == cb.from_user.id, BrandSubscription.brand_id == bid))
        ).first() is not None
    sub_text = "🔕 Отписаться" if is_sub else "🔔 Подписаться"
    rows.append([InlineKeyboardButton(text=f"{sub_text} {brand.title}", callback_data=f"bsub:{bid}")])
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


@router.callback_query(F.data.startswith("bsub:"))
async def cb_brand_sub(cb: CallbackQuery):
    bid = int(cb.data.split(":")[1])
    from app.models import BrandSubscription

    async with SessionMaker() as s:
        brand = await s.get(Brand, bid)
        if not brand:
            await cb.answer("Бренд не найден", show_alert=True)
            return
        sub = (
            await s.scalars(select(BrandSubscription).where(BrandSubscription.user_id == cb.from_user.id, BrandSubscription.brand_id == bid))
        ).first()
        if sub:
            await s.delete(sub)
            await s.commit()
            await cb.answer(f"Отписался от {brand.title}")
        else:
            s.add(BrandSubscription(user_id=cb.from_user.id, brand_id=bid))
            await s.commit()
            await cb.answer(f"Подписался на {brand.title} 🔔")
    # обновляем клавиатуру
    await cb_brand(cb)


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


async def send_box(message: Message):
    from app.models import BoxSubscription

    async with SessionMaker() as s:
        sub = (await s.scalars(select(BoxSubscription).where(BoxSubscription.user_id == message.from_user.id, BoxSubscription.is_active == True))).first()
    if sub:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отписаться", callback_data="box_unsub")]])
        await message.answer("📦 <b>NORM BOX</b> — ты уже подписан! Каждый месяц 3 вещи-сюрприз за 5990₽. Отпишись если не нужен.", reply_markup=kb)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Подписаться 5990₽/мес", callback_data="box_sub")]])
    await message.answer(
        "📦 <b>NORM BOX — подписка</b>\n\nКаждый месяц 3 рандомные вещи (худи/кроссы/аксы) за <b>5990₽</b>.\nЛимитированные дропы — только для подписчиков BOX.\n\nЖми и подпишись!",
        reply_markup=kb,
    )


async def send_wheel(message: Message):
    from datetime import datetime, timedelta

    from app.models import FortuneSpin

    async with SessionMaker() as s:
        last = (
            await s.scalars(select(FortuneSpin).where(FortuneSpin.user_id == message.from_user.id).order_by(FortuneSpin.id.desc()))
        ).first()
        can_spin = True
        if last and last.created_at.date() == datetime.utcnow().date():
            can_spin = False
    if not can_spin:
        await message.answer("🎡 Ты уже крутил сегодня — возвращайся завтра и попытай удачу снова!", reply_markup=main_menu())
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎡 Крутить!", callback_data="wheel_spin")]])
    await message.answer(
        "🎡 <b>Колесо фортуны NORMWEAR</b>\n\nКрути раз в день и выигрывай:\n• 5% / 10% / 15% скидка\n• +50 / +100 бонусов\n\nЖми и испытай удачу!",
        reply_markup=kb,
    )


@router.callback_query(F.data == "wheel_spin")
async def cb_wheel_spin(cb: CallbackQuery):
    import random
    from datetime import datetime

    from app.models import FortuneSpin

    async with SessionMaker() as s:
        last = (
            await s.scalars(select(FortuneSpin).where(FortuneSpin.user_id == cb.from_user.id).order_by(FortuneSpin.id.desc()))
        ).first()
        if last and last.created_at.date() == datetime.utcnow().date():
            await cb.answer("Уже крутил сегодня", show_alert=True)
            return
        prizes = [
            ("5%", 5, 30),
            ("10%", 10, 25),
            ("15%", 15, 15),
            ("+50", 50, 15),
            ("+100", 100, 10),
            ("20%", 20, 5),
        ]
        # weighted choice
        pool = []
        for name, val, w in prizes:
            pool.extend([(name, val)] * w)
        prize_name, prize_val = random.choice(pool)
        # выдаём
        text = ""
        if prize_name.startswith("+"):
            # бонусы
            user = await s.get(User, cb.from_user.id)
            if user:
                user.bonus_points = (user.bonus_points or 0) + prize_val
                from app.models import LoyaltyTransaction

                s.add(LoyaltyTransaction(user_id=user.id, points=prize_val, kind="wheel", note=f"Колесо {prize_name}"))
            s.add(FortuneSpin(user_id=cb.from_user.id, prize=prize_name, value=prize_val))
            await s.commit()
            text = f"🎉 Выпало <b>{prize_name} бонусов</b>! Начислено на счёт."
        else:
            # промокод
            code = f"WHEEL{prize_val}_{cb.from_user.id % 10000}{random.randint(10,99)}"
            # уникальность
            exists = (await s.scalars(select(PromoCode).where(PromoCode.code == code))).first()
            if not exists:
                s.add(PromoCode(code=code, kind="percent", value=float(prize_val), min_order=2000, max_uses=1))
            s.add(FortuneSpin(user_id=cb.from_user.id, prize=prize_name, value=prize_val))
            await s.commit()
            text = f"🎉 Выпало <b>{prize_name} скидка</b>!\nТвой промокод: <code>{code}</code>\nВставь при оформлении — действует 1 раз."
        # стрик 3 дня подряд = +100
        try:
            from datetime import timedelta

            spins = (
                await s.scalars(select(FortuneSpin).where(FortuneSpin.user_id == cb.from_user.id).order_by(FortuneSpin.id.desc()).limit(5))
            ).all()
            days = sorted({sp.created_at.date() for sp in spins}, reverse=True)
            today = datetime.utcnow().date()
            streak = 0
            for i, d in enumerate(days):
                if d == today - timedelta(days=i):
                    streak += 1
                else:
                    break
            if streak >= 3:
                user2 = await s.get(User, cb.from_user.id)
                if user2:
                    user2.bonus_points = (user2.bonus_points or 0) + 100
                    from app.models import LoyaltyTransaction

                    s.add(LoyaltyTransaction(user_id=user2.id, points=100, kind="wheel_streak", note="Стрик 3 дня"))
                    await s.commit()
                    text += "\n\n🔥 Стрик 3 дня! +100 бонусов."
            elif streak == 2:
                text += "\n\nЗавтра крути снова — будет стрик 3 дня и +100."
        except Exception:
            pass
    try:
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="home")]]))
    except Exception:
        await cb.message.answer(text)
    await cb.answer()


@router.callback_query(F.data == "box_sub")
async def cb_box_sub(cb: CallbackQuery):
    from app.models import BoxSubscription

    async with SessionMaker() as s:
        exists = (await s.scalars(select(BoxSubscription).where(BoxSubscription.user_id == cb.from_user.id, BoxSubscription.is_active == True))).first()
        if exists:
            await cb.answer("Уже подписан", show_alert=True)
            return
        s.add(BoxSubscription(user_id=cb.from_user.id, plan="monthly"))
        await s.commit()
    await cb.answer("Подписался на BOX ✅")
    await cb.message.answer("📦 Подписка NORM BOX оформлена! Каждый месяц 3 вещи за 5990₽. Менеджер свяжется для оплаты.")


@router.callback_query(F.data == "box_unsub")
async def cb_box_unsub(cb: CallbackQuery):
    from app.models import BoxSubscription

    async with SessionMaker() as s:
        sub = (await s.scalars(select(BoxSubscription).where(BoxSubscription.user_id == cb.from_user.id, BoxSubscription.is_active == True))).first()
        if sub:
            sub.is_active = False
            await s.commit()
            await cb.answer("Отписался")
            await cb.message.answer("📦 Отписался от BOX.")
        else:
            await cb.answer("Нет подписки", show_alert=True)


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


@router.message(ReviewFSM.text, F.photo)
async def st_review_photo(message: Message, state: FSMContext):
    # UGC: фото-отзыв
    oid = pending_review.get(message.from_user.id)
    if oid is None:
        await state.clear()
        return
    file_id = message.photo[-1].file_id if message.photo else None
    text = (message.caption or "").strip()[:1000] or "Фото-отзыв"
    async with SessionMaker() as s:
        from app.models import Review, User as U, LoyaltyTransaction

        order = await s.get(Order, oid)
        if order is None or order.user_id != message.from_user.id or order.status != "completed":
            await message.answer("Заказ не найден или не завершён.")
            await state.clear()
            pending_review.pop(message.from_user.id, None)
            return
        exists = (await s.scalars(select(Review).where(Review.order_id == oid))).first()
        if exists:
            await message.answer("Уже есть отзыв.")
            await state.clear()
            pending_review.pop(message.from_user.id, None)
            return
        s.add(Review(user_id=message.from_user.id, order_id=oid, text=text, rating=5, is_published=False))
        user = await s.get(U, message.from_user.id)
        if user:
            user.bonus_points = (user.bonus_points or 0) + 50
            s.add(LoyaltyTransaction(user_id=user.id, order_id=oid, points=50, kind="review", note=f"Бонус за фото-отзыв {oid}"))
        await s.commit()
    await state.clear()
    pending_review.pop(message.from_user.id, None)
    await message.answer("Спасибо за фото-отзыв! +50 бонусов 🎁", reply_markup=main_menu())
    # пост в канал как UGC
    try:
        uname = f"@{message.from_user.username}" if message.from_user.username else "клиент"
        cap = f"⭐️ <b>Отзыв</b> {html.escape(uname)}\n{html.escape(text[:500])}"
        if file_id and notify.admin_bot and settings.shop_channel_id:
            await notify.admin_bot.send_photo(chat_id=settings.shop_channel_id, photo=file_id, caption=cap)
        else:
            await notify.notify_admins(f"⭐️ UGC отзыв #{oid}: {html.escape(text[:500])}")
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


@router.message(StateFilter(None), F.voice)
async def voice_search(message: Message):
    # пока без SpeechKit — просим текстом, но сразу подсказываем поиск
    await message.answer(
        "🎙 Голос принят! Распознавание скоро включим.\n"
        "А пока напиши текстом, например:\n"
        "<code>найди худи до 10000</code> или <code>Bape M</code> — сразу покажу.",
        reply_markup=main_menu(),
    )


async def try_text_search(message: Message) -> bool:
    import re

    raw = (message.text or "").strip()
    t = raw.lower()
    search_triggers = ("найди", "найти", "покажи", "покажите", "ищи", "ищу", "есть", "худи", "кросс", "футбол", "штаны", "куртк", "кепк", "до ")
    is_search = any(x in t for x in search_triggers)
    if not is_search:
        # бренд в тексте = тоже поиск
        from app.services.parser import BASE_BRANDS

        is_search = any(kw in t for kw in BASE_BRANDS.keys())
    if not is_search:
        return False
    # цена "до 10000"
    max_price = None
    m = re.search(r"до\s*(\d[\d\s.]*)", t)
    if m:
        try:
            max_price = float(m.group(1).replace(" ", "").replace(".", ""))
        except Exception:
            max_price = None
    # бренд
    brand_kw = None
    from app.services.parser import BASE_BRANDS as _BB

    for kw, title in _BB.items():
        if kw in t:
            brand_kw = title
            break
    async with SessionMaker() as s:
        q = select(Product).where(Product.status == "published").order_by(Product.id.desc()).limit(6)
        if max_price:
            q = select(Product).where(Product.status == "published", Product.retail_price <= max_price).order_by(Product.retail_price.desc()).limit(6)
        prods = (await s.scalars(q)).all()
        # фильтр по бренду если нашли
        if brand_kw:
            filtered = []
            for p in prods:
                b = await s.get(Brand, p.brand_id) if p.brand_id else None
                if b and b.title.lower() == brand_kw.lower():
                    filtered.append(p)
            # если по бренду пусто — ищем по всем
            if filtered:
                prods = filtered
            else:
                all_q = select(Product).where(Product.status == "published").order_by(Product.id.desc()).limit(20)
                all_prods = (await s.scalars(all_q)).all()
                by_brand = []
                for p in all_prods:
                    b = await s.get(Brand, p.brand_id) if p.brand_id else None
                    if b and b.title.lower() == brand_kw.lower() and (not max_price or (p.retail_price or 0) <= max_price):
                        by_brand.append(p)
                prods = by_brand[:6]
        if not prods:
            await message.answer("Ничего не нашёл — попробуй проще, например <code>Bape</code> или <code>худи до 5000</code>.", reply_markup=main_menu())
            return True
        rows = []
        for p in prods:
            rows.append([InlineKeyboardButton(text=f"{p.title[:30]} · {int(p.retail_price or 0)}₽", callback_data=f"pr:{p.id}")])
        rows.append([InlineKeyboardButton(text="🛍 Весь каталог", callback_data="cat")])
        hint = f" до {int(max_price)}₽" if max_price else ""
        await message.answer(f"🔎 Нашёл{hint} — выбирай:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return True


@router.message(StateFilter(None), F.photo)
async def stylist_photo(message: Message):
    # AI-стилист — пока random 3 товара, позже CLIP
    async with SessionMaker() as s:
        prods = (await s.scalars(select(Product).where(Product.status == "published").order_by(func.random()).limit(3))).all()
        if not prods:
            await message.answer("Каталог пока пуст — скоро будут новинки!")
            return
        lines = ["👗 <b>AI-стилист NORMWEAR</b> — вот что нашёл похожее:", ""]
        kb_rows = []
        for p in prods:
            brand = await s.get(Brand, p.brand_id) if p.brand_id else None
            bname = brand.title if brand else ""
            lines.append(f"• <b>{html.escape(bname)}</b> {html.escape(p.title)} — <b>{int(p.retail_price or 0)}₽</b>")
            kb_rows.append([InlineKeyboardButton(text=f"{p.title[:28]} · {int(p.retail_price or 0)}₽", callback_data=f"pr:{p.id}")])
        kb_rows.append([InlineKeyboardButton(text="🛍 Весь каталог", callback_data="cat")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.message(StateFilter(None), F.text & ~F.command)
async def support_message(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in MENU:
        return
    if message.from_user.is_bot:
        return
    # сначала пробуем как поиск, иначе — тикет в поддержку
    try:
        if await try_text_search(message):
            return
    except Exception:
        pass
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