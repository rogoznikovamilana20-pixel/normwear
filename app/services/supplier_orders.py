"""
Автоматическая отправка заказов поставщику
"""

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionMaker
from app.models import Order, OrderItem, Product

log = logging.getLogger("supplier_orders")
settings = get_settings()


class SupplierOrderSender:
    """Отправка заказов поставщику"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.supplier_id = settings.supplier_telegram_id if hasattr(settings, "supplier_telegram_id") else None

    async def send_order_to_supplier(self, order_id: int) -> bool:
        """Отправить заказ поставщику"""
        if not self.supplier_id:
            log.warning("SUPPLIER_TELEGRAM_ID не настроен")
            return False

        try:
            async with SessionMaker() as s:
                order = await s.get(Order, order_id)
                if not order:
                    log.error("Заказ %s не найден", order_id)
                    return False

                if order.status != "awaiting_payment":
                    log.warning("Заказ %s не в статусе awaiting_payment: %s", order_id, order.status)
                    return False

                # Формируем сообщение для поставщика
                message = await self._format_order_message(order, s)

                # Создаём клавиатуру для поставщика
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="✅ Отправлено", callback_data=f"supplier_ship:{order_id}"),
                            InlineKeyboardButton(text="❌ Проблема", callback_data=f"supplier_issue:{order_id}"),
                        ],
                        [InlineKeyboardButton(text="📦 Добавить трек", callback_data=f"supplier_track:{order_id}")],
                    ]
                )

                # Отправляем поставщику
                await self.bot.send_message(
                    self.supplier_id,
                    message,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

                log.info("Заказ %s отправлен поставщику %s", order_id, self.supplier_id)
                return True

        except Exception as e:
            log.error("Ошибка отправки заказа %s поставщику: %s", order_id, e)
            return False

    async def _format_order_message(self, order: Order, session) -> str:
        """Формирование сообщения заказа для поставщика"""
        lines = [
            f"🆕 <b>НОВЫЙ ЗАКАЗ #{order.id}</b>",
            "",
            f"👤 <b>Клиент:</b> {order.full_name}",
            f"📱 <b>Телефон:</b> {order.phone}",
            f"🏙 <b>Город:</b> {order.city}",
            f"📍 <b>Адрес:</b> {order.address}",
            "",
            f"💰 <b>Сумма:</b> {int(order.total)} ₽",
            "",
            "<b>📦 Товары:</b>",
        ]

        # Получаем товары заказа
        items = await session.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        items = items.scalars().all()

        for item in items:
            product = await session.get(Product, item.product_id)
            product_name = product.title if product else "Товар не найден"
            lines.append(f"• {product_name} · {item.size} · {int(item.price)} ₽ · {item.quantity} шт")

        lines += ["", "<b>🚚 Нужно отправить по адресу:</b>", f"{order.city}, {order.address}", "", "<b>📞 Связаться с клиентом:</b>", f"{order.phone}"]

        return "\n".join(lines)

    async def handle_supplier_response(self, callback_data: str, user_id: int) -> str:
        """Обработка ответа поставщика"""
        if user_id != self.supplier_id:
            return "❌ Только поставщик может отвечать на заказы"

        try:
            action, order_id = callback_data.split(":")
            order_id = int(order_id)

            async with SessionMaker() as s:
                order = await s.get(Order, order_id)
                if not order:
                    return "❌ Заказ не найден"

                if action == "supplier_ship":
                    # Поставщик отправил заказ
                    order.status = "shipped"
                    await s.commit()

                    # Уведомляем клиента
                    await self._notify_client_shipped(order)

                    # Уведомляем админов
                    await self._notify_admins_shipped(order)

                    return f"✅ Заказ #{order_id} отмечен как отправлен"

                elif action == "supplier_issue":
                    # Проблема с заказом
                    order.status = "awaiting_delivery"
                    await s.commit()

                    # Уведомляем админов о проблеме
                    await self._notify_admins_issue(order)

                    return f"⚠️ Заказ #{order.id} отмечен как проблемный"

                elif action == "supplier_track":
                    # Запрос трек-номера
                    return f"📦 Введите трек-номер для заказа #{order.id} (ответьте сообщением):"

        except Exception as e:
            log.error("Ошибка обработки ответа поставщика: %s", e)
            return f"❌ Ошибка: {str(e)}"

    async def _notify_client_shipped(self, order: Order):
        """Уведомить клиента об отправке"""
        try:
            await self.bot.send_message(
                order.user_id,
                f"🚀 <b>Ваш заказ #{order.id} отправлен!</b>\n\n"
                f"📦 Курьер скоро свяжется с вами\n"
                f"📍 Адрес доставки: {order.city}, {order.address}\n\n"
                f"Отслеживайте статус в «📦 Мои заказы»",
            )
        except Exception as e:
            log.error("Ошибка уведомления клиента %s: %s", order.user_id, e)

    async def _notify_admins_shipped(self, order: Order):
        """Уведомить админов об отправке"""
        from app.services import notify

        message = f"🚚 <b>Заказ #{order.id} отправлен поставщиком</b>\n\n" f"👤 {order.full_name}\n" f"📍 {order.city}, {order.address}\n\n" f"Статус обновлён автоматически"

        await notify.notify_admins(message)

    async def _notify_admins_issue(self, order: Order):
        """Уведомить админов о проблеме"""
        from app.services import notify

        message = f"⚠️ <b>Проблема с заказом #{order.id}</b>\n\n" f"👤 {order.full_name}\n" f"📍 {order.city}, {order.address}\n\n" f"Поставщик отметил проблему. Нужна проверка."

        await notify.notify_admins(message)


async def send_new_orders_to_supplier(bot: Bot):
    """Отправить все необработанные заказы поставщику"""
    sender = SupplierOrderSender(bot)

    async with SessionMaker() as s:
        # Получаем заказы в статусе awaiting_payment, которые ещё не отправлены поставщику
        orders = await s.execute(
            select(Order).where(Order.status == "awaiting_payment", Order.supplier_notified == False)
        )
        orders = orders.scalars().all()

        for order in orders:
            success = await sender.send_order_to_supplier(order.id)
            if success:
                order.supplier_notified = True
                await s.commit()

    return len(orders)
