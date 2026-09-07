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


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    # фаза 10: подтверждаем все счета, сумму проверяет Telegram
    try:
        await query.answer(ok=True)
    except Exception:
        pass


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    pay = message.successful_payment
    if pay is None:
        return
    payload = (pay.invoice_payload or "")
    kind, _, ref = payload.partition(":")
    async with SessionMaker() as s:
        if kind == "order":
            try:
                oid = int(ref)
            except ValueError:
                return
            order = await s.get(Order, oid)
            if order is None or order.user_id != message.from_user.id:
                return
            exists = (await s.scalars(select(Payment).where(Payment.order_id == oid, Payment.status == "paid"))).first()
            if exists is None:
                s.add(
                    Payment(
                        order_id=oid,
                        method="telegram",
                        amount=float(pay.total_amount) / 100,
                        status="paid",
                        provider_ref=pay.telegram_payment_charge_id,
                    )
                )
                await s.commit()
            await message.answer(f"✅ Оплата заказа №{oid} прошла! Менеджер свяжется по доставке.", reply_markup=main_menu())
            await notify.notify_admins(f"💰 <b>Заказ №{oid} ОПЛАЧЕН</b> ({float(pay.total_amount)/100:.0f}₽). Можно отправлять: введите трек.")
        elif kind == "box":
            from app.models import LoyaltyTransaction

            sub = (await s.scalars(select(BoxSubscription).where(BoxSubscription.user_id == message.from_user.id, BoxSubscription.is_active == True))).first()
            if sub is None:
                sub = BoxSubscription(user_id=message.from_user.id, plan="monthly")
                s.add(sub)
            s.add(
                LoyaltyTransaction(
                    user_id=message.from_user.id,
                    order_id=None,
                    points=0,
                    kind="box",
                    note=f"BOX оплачен {float(pay.total_amount)/100:.0f}₽ ({pay.telegram_payment_charge_id})",
                )
            )
            await s.commit()
            await message.answer("📦 Подписка NORM BOX активна! Каждый месяц 3 вещи за 5990₽.", reply_markup=main_menu())
            await notify.notify_admins(f"📦 <b>Новая подписка BOX</b> — user <code>{message.from_user.id}</code>, оплата прошла.")


