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
    # UGC: фото-отзыв (фото качаем сразу — file_id чужого бота в канал не уйдёт)
    from pathlib import Path

    from app.config import BASE_DIR

    oid = pending_review.get(message.from_user.id)
    if oid is None:
        await state.clear()
        return
    text = (message.caption or "").strip()[:1000] or "Фото-отзыв"
    local_path = ""
    try:
        bot = message.bot
        f = await bot.get_file(message.photo[-1].file_id)
        buf = await bot.download_file(f.file_path)
        rdir = BASE_DIR / "data" / "reviews"
        rdir.mkdir(parents=True, exist_ok=True)
        local_path = str(rdir / f"rev_{oid}.jpg")
        Path(local_path).write_bytes(buf.read())
    except Exception:
        local_path = ""
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
        rev = Review(user_id=message.from_user.id, order_id=oid, text=text, rating=5, is_published=False)
        s.add(rev)
        await s.flush()
        rid = rev.id
        user = await s.get(U, message.from_user.id)
        if user:
            user.bonus_points = (user.bonus_points or 0) + 50
            s.add(LoyaltyTransaction(user_id=user.id, order_id=oid, points=50, kind="review", note=f"Бонус за фото-отзыв {oid}"))
        await s.commit()
    await state.clear()
    pending_review.pop(message.from_user.id, None)
    await message.answer("Спасибо за фото-отзыв! +50 бонусов 🎁 После проверки выложим в канал.", reply_markup=main_menu())
    # на модерацию админам
    uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ В канал", callback_data=f"rv_pub:{rid}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rv_del:{rid}")],
        ]
    )
    await notify.notify_admins(
        f"⭐️ <b>Фото-отзыв на модерации</b> (заказ №{oid})\n"
        f"👤 {html.escape(message.from_user.first_name or '')} {uname}\n\n{html.escape(text[:800])}",
        kb,
    )


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
        rev = Review(user_id=message.from_user.id, order_id=oid, text=text, rating=5)
        s.add(rev)
        await s.flush()
        rid = rev.id
        user = await s.get(U, message.from_user.id)
        if user:
            user.bonus_points = (user.bonus_points or 0) + 50
            s.add(LoyaltyTransaction(user_id=user.id, order_id=oid, points=50, kind="review", note=f"Бонус за отзыв к заказу №{oid}"))
        await s.commit()
    await state.clear()
    pending_review.pop(message.from_user.id, None)
    await message.answer("Спасибо за отзыв! +50 бонусов начислено 🎁 После проверки выложим в канал.", reply_markup=main_menu())
    # на модерацию админам
    uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ В канал", callback_data=f"rv_pub:{rid}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rv_del:{rid}")],
        ]
    )
    await notify.notify_admins(
        f"⭐️ <b>Отзыв на модерации</b> (заказ №{oid})\n👤 {html.escape(message.from_user.first_name or '')} {uname} · <code>{message.from_user.id}</code>\n\n{html.escape(text[:800])}",
        kb,
    )


