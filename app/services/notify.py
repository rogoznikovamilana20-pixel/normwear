from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.config import get_settings

shop_bot: Bot | None = None
admin_bot: Bot | None = None

ORDER_STATUS_TEXT = {
    "awaiting_delivery": "📦 Заказ №{id} принят! Ждём поступения товара на склад — сообщим, когда он будет готов к оплате.",
    "awaiting_payment": "💰 Заказ №{id} готов к оплате! Сумма: {total} ₽. Реквизиты для оплаты пришлёт менеджер.",
    "shipped": "🚚 Заказ №{id} отправлен! 📮 Трек-номер: {tracking}",
    "delivered": "📬 Заказ №{id} доставлен. Проверьте, пожалуйста, товар.",
    "completed": "✅ Заказ №{id} завершён. Спасибо за покупку! 🎁 Начислено бонусов: +{bonus}",
    "cancelled": "❌ Заказ №{id} отменён.",
}

ORDER_STATUS_LABEL = {
    "awaiting_delivery": "Ждёт поступления",
    "awaiting_payment": "К оплате",
    "shipped": "Отправлен",
    "delivered": "Доставлен",
    "completed": "Завершён",
    "cancelled": "Отменён",
}


async def notify_user(user_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if shop_bot is None:
        return
    try:
        await shop_bot.send_message(user_id, text, reply_markup=reply_markup)
    except Exception:
        pass


async def notify_admins(text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if admin_bot is None:
        return
    for admin_id in get_settings().admin_ids:
        try:
            await admin_bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception:
            pass


def order_status_text(order) -> str:
    tpl = ORDER_STATUS_TEXT.get(order.status, "Заказ №{id}: статус обновлён.")
    return tpl.format(
        id=order.id,
        total=int(order.total),
        tracking=order.tracking_number or "уточняется",
        bonus=order.bonus_earned,
    )


async def notify_drop_subscribers(product_title: str, product_id: int) -> int:
    if shop_bot is None:
        return 0
    from app.db import SessionMaker
    from sqlalchemy import select
    from app.models import DropSubscription

    async with SessionMaker() as s:
        subs = (await s.scalars(select(DropSubscription))).all()
        ids = [sub.user_id for sub in subs]
    if not ids:
        return 0
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛍 Открыть", callback_data=f"pr:{product_id}")]])
    text = f"🔥 <b>Новый дроп</b> — {product_title}\n\nЖми чтобы посмотреть первым:"
    sent = 0
    for uid in ids:
        try:
            await shop_bot.send_message(uid, text, reply_markup=kb)
            sent += 1
        except Exception:
            continue
    return sent


async def request_review(order) -> None:
    if shop_bot is None:
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Оставить отзыв", callback_data=f"rev:{order.id}")],
            [InlineKeyboardButton(text="Позже", callback_data="rev_skip")],
        ]
    )
    try:
        await shop_bot.send_message(
            order.user_id,
            f"✅ Заказ №{order.id} завершён!\n\nПонравилось? Оставь отзыв — получишь <b>+50 бонусов</b> и поможешь другим с выбором.",
            reply_markup=kb,
        )
    except Exception:
        pass