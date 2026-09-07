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


