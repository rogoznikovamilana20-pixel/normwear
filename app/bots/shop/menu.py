import html

from aiogram import F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, PreCheckoutQuery, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionMaker
from app.models import BoxSubscription, Brand, CartItem, FortuneSpin, Order, Payment, Product, ProductPhoto, PromoCode, SupportMessage, SupportTicket, User
from app.services import giveaway as giveaway_svc
from app.services import notify, orders
from app.services.photos import yandex_library
from app.bots.shop import Checkout, MENU, ReviewFSM, WELCOME, cancel_kb, main_menu, menu_inline, miniapp_available, pending_review, router, send_menu, settings
from app.bots.shop.catalog import send_brands
from app.bots.shop.cart import render_cart
from app.bots.shop.loyalty import send_bonuses, send_box, send_drops, send_wheel

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
    if payload == "opt":
        from app.bots.shop.opt import cmd_opt as _cmd_opt

        await _cmd_opt(message, state)
        return
    if payload.startswith("remind_"):
        try:
            tid = int(payload.split("_", 1)[1])
        except ValueError:
            tid = 0
        from app.models import DropReminder, DropTimer

        async with SessionMaker() as s:
            t = await s.get(DropTimer, tid) if tid else None
            if t is None or not t.is_active:
                await message.answer("Этот таймер уже прошёл. Следи за каналом — будут новые 🔔")
                return
            has = (
                await s.scalars(
                    select(DropReminder).where(DropReminder.timer_id == tid, DropReminder.user_id == message.from_user.id)
                )
            ).first()
            if has is None:
                s.add(DropReminder(user_id=message.from_user.id, timer_id=tid))
                await s.commit()
        await message.answer(f"🔔 Готово, напомним: <b>{t.title}</b> — пуш прилетит в этот чат.", reply_markup=main_menu())
        return
    if payload == "give1" or payload.startswith("give1_r"):
        inviter_id = 0
        if payload.startswith("give1_r"):
            try:
                inviter_id = int(payload[7:])
            except ValueError:
                inviter_id = 0
        await message.answer(WELCOME, reply_markup=main_menu())
        async with SessionMaker() as s:
            gw = await giveaway_svc.active(s)
            if gw is None:
                await message.answer("Этот розыгрыш уже завершён. Следи за каналом — будут новые 🔥")
                return
            sub = await giveaway_svc.is_subscribed(message.bot, settings.shop_channel_id, message.from_user.id)
            if not sub:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{settings.shop_channel_username}")],
                ])
                await message.answer("Чтобы участвовать — подпишись на канал и нажми «🎲 Участвую» ещё раз 👇", reply_markup=kb)
                return
            new = await giveaway_svc.enter(s, gw, message.from_user.id)
            if new and inviter_id:
                await giveaway_svc.record_referral(s, gw, inviter_id, message.from_user.id)
            brought = await giveaway_svc.invite_count(s, gw, message.from_user.id)
            link = f"https://t.me/{settings.shop_username}?start=give1_r{message.from_user.id}"
        tickets = 1 + brought
        extra = f"\n\n👥 Твоя ссылка для друзей:\n{link}\nЗа каждого друга +1 шанс (у тебя билетов: {tickets})."
        await message.answer("🎲 Ты уже в игре, жди финал!" + extra if not new else "🎲 Готово, ты в игре! Победителей выберем рандомайзером среди подписчиков. Удачи 🤝" + extra)
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


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено. Главное меню:", reply_markup=main_menu())


@router.message(F.text.in_(MENU))
async def menu_texts(message: Message, state: FSMContext):
    await state.clear()
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


