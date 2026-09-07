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
from app.bots.shop import Checkout, MENU, ReviewFSM, WELCOME, cancel_kb, main_menu, menu_inline, miniapp_available, pending_review, router, send_menu, settings

@router.message(StateFilter(None), F.voice)
async def voice_search(message: Message):
    # офлайн-распознавание (Vosk) — без внешних API
    from app.services import speech as speech_svc

    if not speech_svc.is_available() or not message.voice:
        await message.answer(
            "🎙 Голос принят! Напиши текстом, например:\n"
            "<code>найди худи до 10000</code> или <code>Bape M</code> — сразу покажу.",
            reply_markup=main_menu(),
        )
        return
    await message.answer("🎙 Слушаю...")
    try:
        bot = message.bot
        f = await bot.get_file(message.voice.file_id)
        buf = await bot.download_file(f.file_path)
        heard = await __import__("asyncio").to_thread(speech_svc.transcribe_ogg, buf.read())
    except Exception:
        heard = None
    if not heard:
        await message.answer("Не расслышал — напиши текстом, например <code>найди худи до 10000</code>.")
        return
    await message.answer(f"🎙 Услышал: «{html.escape(heard)}»")
    try:
        if await try_text_search(message, text_override=heard):
            return
    except Exception:
        pass
    await message.answer("Ничего не нашёл — попробуй проще, например <code>Bape</code>.")


async def try_text_search(message: Message, text_override: str | None = None) -> bool:
    import re

    raw = (text_override if text_override is not None else (message.text or "")).strip()
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
    # стилист: dominant-цвет фото -> товары в том же цвете
    from app.services import stylist as stylist_svc

    await message.answer("👗 Смотрю фото...")
    try:
        bot = message.bot
        f = await bot.get_file(message.photo[-1].file_id)
        buf = await bot.download_file(f.file_path)
        img_bytes = buf.read()
    except Exception:
        img_bytes = b""
    async with SessionMaker() as s:
        if img_bytes:
            prods, fam = await stylist_svc.match_products(s, img_bytes, limit=3)
        else:
            prods, fam = [], None
        if not prods:
            await message.answer("Каталог пока пуст — скоро будут новинки!")
            return
        fam_ru = {"black": "чёрный", "white": "белый", "grey": "серый", "blue": "синий", "red": "красный", "green": "зелёный", "beige": "бежевый", "brown": "коричневый", "pink": "розовый"}.get(fam or "", "")
        head = f"👗 Вижу {fam_ru} — вот похожее из каталога:" if fam_ru else "👗 Вот что нашёл похожее:"
        lines = [f"<b>{head}</b>", ""]
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
    # команда без entity (Telegram иногда шлёт /start как обычный текст) — обрабатываем как старт
    if text.startswith("/"):
        from app.bots.shop.menu import cmd_start

        await cmd_start(message, state)
        return
    # FAQ вперёд: частые вопросы отвечаем сразу, тикет только по кнопке
    try:
        from app.bots.shop.faq import match_faq, pending_faq, send_faq_answer

        key = match_faq(text)
        if key:
            pending_faq[message.from_user.id] = text
            await send_faq_answer(message, key)
            return
    except Exception:
        pass
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