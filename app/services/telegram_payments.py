"""
Интеграция с Telegram Payments для автоматической оплаты заказов
"""

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import PreCheckoutQuery, Message, SuccessfulPayment
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionMaker
from app.models import Order, Payment
from app.services import notify, orders

log = logging.getLogger("telegram_payments")
settings = get_settings()


async def handle_pre_checkout_query(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    """Обработка предварительного запроса на оплату"""
    try:
        order_id = int(pre_checkout_query.invoice_payload)

        async with SessionMaker() as s:
            order = await s.get(Order, order_id)

            if not order:
                await pre_checkout_query.answer(ok=False, error_message="Заказ не найден")
                return

            if order.status != "awaiting_payment":
                await pre_checkout_query.answer(ok=False, error_message="Заказ уже оплачен или отменён")
                return

            # Проверяем сумму (защита от манипуляций)
            expected_amount = int(order.total * 100)  # Telegram использует копейки
            if pre_checkout_query.total_amount != expected_amount:
                await pre_checkout_query.answer(ok=False, error_message="Несовпадение суммы")
                return

        # Всё ок, разрешаем оплату
        await pre_checkout_query.answer(ok=True)
        log.info("PreCheckout approved for order %s", order_id)

    except Exception as e:
        log.error("Ошибка обработки PreCheckoutQuery: %s", e)
        await pre_checkout_query.answer(ok=False, error_message="Ошибка обработки оплаты")


async def handle_successful_payment(successful_payment: SuccessfulPayment, bot: Bot):
    """Обработка успешной оплаты"""
    try:
        order_id = int(successful_payment.invoice_payload)
        telegram_payment_charge_id = successful_payment.telegram_payment_charge_id

        async with SessionMaker() as s:
            order = await s.get(Order, order_id)

            if not order:
                log.error("Order not found for successful payment: %s", order_id)
                return

            # Обновляем статус заказа
            from app.models import ORDER_NEXT

            order.status = ORDER_NEXT.get(order.status, "shipped")
            order.updated_at = order.__class__.updated_at.default()

            # Создаём запись о платеже
            payment = Payment(
                order_id=order.id,
                method="telegram",
                amount=order.total,
                status="completed",
                provider_ref=telegram_payment_charge_id,
            )
            s.add(payment)

            # Начисляем бонусы
            await orders.process_loyalty(s, order)

            await s.commit()

        log.info("Payment successful for order %s, status: %s", order_id, order.status)

        # Отправляем уведомление клиенту
        if successful_payment.user:
            await bot.send_message(
                successful_payment.user.id,
                f"✅ Оплата прошла успешно!\n\n"
                f"Заказ №{order_id} подтверждён.\n"
                f"Статус: {order.status}\n\n"
                f"📦 Мы скоро начнём подготовку заказа.",
            )

        # Отправляем уведомление админам
        if notify.admin_bot:
            for admin_id in settings.admin_ids:
                try:
                    await notify.admin_bot.send_message(
                        admin_id,
                        f"💰 <b>Новая оплата!</b>\n\n"
                        f"Заказ №{order_id}\n"
                        f"Сумма: {order.total}₽\n"
                        f"Статус: {order.status}\n"
                        f"Payment ID: {telegram_payment_charge_id}",
                    )
                except Exception as e:
                    log.error("Ошибка отправки уведомления админу %s: %s", admin_id, e)

    except Exception as e:
        log.error("Ошибка обработки успешной оплаты: %s", e)


async def create_invoice(order_id: int, bot: Bot, user_id: int) -> Optional[str]:
    """Создание invoice для оплаты через Telegram Payments"""
    try:
        async with SessionMaker() as s:
            order = await s.get(Order, order_id)

            if not order:
                log.error("Order not found: %s", order_id)
                return None

            if order.status != "awaiting_payment":
                log.warning("Order %s not in awaiting_payment status: %s", order_id, order.status)
                return None

        # Проверяем наличие токена провайдера
        if not settings.telegram_payment_provider_token:
            log.error("TELEGRAM_PAYMENT_PROVIDER_TOKEN не настроен")
            return None

        # Создаём invoice
        title = f"Заказ №{order_id}"
        description = f"Оплата заказа в магазине NORMWEAR на сумму {order.total}₽"
        payload = str(order_id)  # ID заказа для идентификации
        currency = "RUB"
        prices = [
            {
                "label": "Товары",
                "amount": int(order.total * 100),  # в копейках
            }
        ]

        invoice = await bot.create_invoice(
            title=title,
            description=description,
            payload=payload,
            provider_token=settings.telegram_payment_provider_token,
            currency=currency,
            prices=prices,
            need_name=True,
            need_phone_number=True,
            need_email=False,
            need_shipping_address=True,
            send_phone_number_to_provider=True,
            send_email_to_provider=False,
            is_flexible=False,
        )

        log.info("Invoice created for order %s", order_id)
        return invoice.invoice_url

    except Exception as e:
        log.error("Ошибка создания invoice для заказа %s: %s", order_id, e)
        return None


async def check_payment_status(order_id: int) -> str:
    """Проверка статуса платежа заказа"""
    async with SessionMaker() as s:
        order = await s.get(Order, order_id)
        if not order:
            return "not_found"

        # Проверяем платежи
        payment = await s.execute(
            select(Payment).where(Payment.order_id == order_id, Payment.method == "telegram")
        )
        payment = payment.scalar_one_or_none()

        if payment and payment.status == "completed":
            return "paid"
        elif order.status in ("shipped", "delivered", "completed"):
            return "paid"
        else:
            return "pending"
