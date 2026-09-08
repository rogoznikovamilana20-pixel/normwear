import html

from aiogram import F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, PreCheckoutQuery, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionMaker
from app.models import BoxSubscription, Brand, CartItem, FortuneSpin, Order, Payment, Product, ProductPhoto, PromoCode, SupportMessage, SupportTicket, User
from app.services import notify, orders
from app.services.photos import yandex_library
from app.bots.shop import Checkout, MENU, ReviewFSM, StockReq, WELCOME, cancel_kb, main_menu, menu_inline, miniapp_available, pending_review, pending_stock_product, router, send_menu, settings
from app.bots.shop.cart import render_cart

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
    rows.append([InlineKeyboardButton(text="🤍 В избранное", callback_data=f"fav:{pid}"), InlineKeyboardButton(text="📩 Нет моего размера", callback_data=f"streq:{pid}")])
    rows.append([InlineKeyboardButton(text="🔒 Забронировать размер за 199₽", callback_data=f"res:{pid}")])
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
    # fallback 2: локальное фото поставщика (скачано админ-ботом) — шоп-бот загружает сам
    local_path = None
    if photo is None:
        from app.services.photos import resolve_local

        for ph in photos:
            if ph.source == "local":
                local_path = resolve_local(ph.url)
                if local_path:
                    break
    try:
        if photo:
            await cb.message.answer_photo(photo=photo, caption=text, reply_markup=kb)
        elif local_path:
            from aiogram.types import FSInputFile

            await cb.message.answer_photo(photo=FSInputFile(local_path), caption=text, reply_markup=kb)
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


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    from app.models import Favorite

    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None or p.status != "published":
            await cb.answer("Товар недоступен", show_alert=True)
            return
        exists = (
            await s.scalars(select(Favorite).where(Favorite.user_id == cb.from_user.id, Favorite.product_id == pid))
        ).first()
        if exists:
            await s.delete(exists)
            await s.commit()
            await cb.answer("Убрал из избранного")
        else:
            s.add(Favorite(user_id=cb.from_user.id, product_id=pid))
            await s.commit()
            await cb.answer("🤍 В избранном! Смотри в ☰ Меню → Избранное")


@router.callback_query(F.data == "m_fav")
async def cb_m_fav(cb: CallbackQuery):
    from app.models import Favorite

    async with SessionMaker() as s:
        favs = (
            await s.scalars(select(Favorite).where(Favorite.user_id == cb.from_user.id).order_by(Favorite.id.desc()).limit(10))
        ).all()
        rows = []
        for f in favs:
            p = await s.get(Product, f.product_id)
            if p is None or p.status != "published":
                continue
            rows.append([InlineKeyboardButton(text=f"{p.title[:30]} · {int(p.retail_price or 0)}₽", callback_data=f"pr:{p.id}")])
    if not rows:
        await cb.message.answer("Избранное пусто — жми 🤍 на карточке товара.", reply_markup=main_menu())
    else:
        rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
        await cb.message.answer("🤍 <b>Избранное</b>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("res:"))
async def cb_reserve(cb: CallbackQuery):
    from datetime import timedelta

    from app.models import Reservation, utcnow

    parts = cb.data.split(":")
    pid = int(parts[1])
    idx = int(parts[2]) if len(parts) > 2 else -1
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None or p.status != "published":
            await cb.answer("Товар недоступен", show_alert=True)
            return
        sizes = [str(x) for x in (p.sizes or [])]
        if idx < 0:
            if not sizes:
                await cb.answer("Безразмерный товар — просто кидай в корзину", show_alert=True)
                return
            rows = []
            for i in range(0, len(sizes), 4):
                rows.append([InlineKeyboardButton(text=sizes[j], callback_data=f"res:{pid}:{j}") for j in range(i, min(i + 4, len(sizes)))])
            await cb.message.answer(f"🔒 <b>Бронь 199₽</b> — {html.escape(p.title)}\nВыбери размер, бронь держится 24 часа и идёт в зачёт заказа:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await cb.answer()
            return
        size = sizes[idx] if idx < len(sizes) else ""
        # одна активная бронь на товар+размер
        exists = (
            await s.scalars(
                select(Reservation).where(
                    Reservation.user_id == cb.from_user.id, Reservation.product_id == pid, Reservation.size == size, Reservation.status.in_(("pending", "active"))
                )
            )
        ).first()
        if exists:
            await cb.answer("У тебя уже есть бронь на этот размер", show_alert=True)
            return
        res = Reservation(
            user_id=cb.from_user.id, product_id=pid, size=size, amount=199, status="pending",
            expires_at=utcnow() + timedelta(hours=24),
        )
        s.add(res)
        await s.flush()
        rid = res.id
        await s.commit()
    from app.services import payments as payments_svc

    if payments_svc.is_configured(settings) and notify.shop_bot is not None:
        ok = await payments_svc.send_reserve_invoice(notify.shop_bot, settings, cb.from_user.id, rid, p.title, size)
        if ok:
            await cb.message.answer("💳 Счёт на бронь 199₽ отправлен — оплати в этом чате, держу размер 24 часа.")
            await cb.answer()
            return
    # ручной режим — бронь активна сразу, менеджер подтвердит 199₽
    async with SessionMaker() as s:
        from app.models import Reservation as _R

        r = await s.get(_R, rid)
        if r is not None:
            r.status = "active"
            await s.commit()
    await notify.notify_admins(f"🔒 <b>Новая бронь #{rid}</b> — {html.escape(p.title)} · {size} · user <code>{cb.from_user.id}</code>. Подтверди 199₽.")
    await cb.message.answer("🔒 Размер забронирован на 24 часа! 199₽ идут в зачёт заказа. Менеджер подтвердит оплату.")
    await cb.answer()


@router.callback_query(F.data.startswith("streq:"))
async def cb_stock_req(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[1])
    async with SessionMaker() as s:
        p = await s.get(Product, pid)
        if p is None:
            await cb.answer("Товар не найден", show_alert=True)
            return
        sizes = ", ".join(str(x) for x in (p.sizes or [])) or "—"
    pending_stock_product[cb.from_user.id] = pid
    await state.set_state(StockReq.size)
    await cb.message.answer(f"📩 Какой размер нужен? Сейчас есть: {html.escape(sizes)}\nНапиши размер одним сообщением (или «-» чтобы следить за любым).")
    await cb.answer()


@router.message(StockReq.size, F.text)
async def st_stock_size(message: Message, state: FSMContext):
    from app.models import StockRequest

    pid = pending_stock_product.get(message.from_user.id)
    size = (message.text or "").strip()[:16]
    await state.clear()
    pending_stock_product.pop(message.from_user.id, None)
    if pid is None or not size:
        return
    if size == "-":
        size = ""
    async with SessionMaker() as s:
        exists = (
            await s.scalars(
                select(StockRequest).where(
                    StockRequest.user_id == message.from_user.id, StockRequest.product_id == pid, StockRequest.size == size, StockRequest.notified == False  # noqa: E712
                )
            )
        ).first()
        if exists is None:
            s.add(StockRequest(user_id=message.from_user.id, product_id=pid, size=size))
            await s.commit()
    await message.answer("📩 Записал! Пришлю пуш как только размер появится.", reply_markup=main_menu())


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


