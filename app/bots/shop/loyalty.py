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
    from app.services import payments as payments_svc

    async with SessionMaker() as s:
        exists = (await s.scalars(select(BoxSubscription).where(BoxSubscription.user_id == cb.from_user.id, BoxSubscription.is_active == True))).first()
        if exists:
            await cb.answer("Уже подписан", show_alert=True)
            return
        # фаза 10: если провайдер настроен — счёт, иначе ручная подписка
        if payments_svc.is_configured(settings) and notify.shop_bot is not None:
            ok = await payments_svc.send_box_invoice(notify.shop_bot, settings, cb.from_user.id)
            await cb.answer("Счёт отправлен 💳" if ok else "Не вышло, свяжись с менеджером", show_alert=not ok)
            if ok:
                await cb.message.answer("💳 Счёт на NORM BOX (5990₽/мес) отправлен — оплати в этом чате.")
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


