import logging

log = logging.getLogger("payments")


def is_configured(settings) -> bool:
    return bool(settings.telegram_payment_provider_token)


async def send_order_invoice(bot, settings, order, items_text: str = "") -> bool:
    """Фаза 10: счёт на оплату заказа. Возвращает False если провайдер не настроен."""
    if not is_configured(settings):
        return False
    from aiogram.types import LabeledPrice

    amount = int(round(float(order.total) * 100))
    if amount <= 0:
        return False
    try:
        await bot.send_invoice(
            chat_id=order.user_id,
            title=f"NORMWEAR — заказ №{order.id}",
            description=(items_text[:200] or f"Оплата заказа №{order.id}") + "\nДоставка 2–4 дня.",
            payload=f"order:{order.id}",
            provider_token=settings.telegram_payment_provider_token,
            currency="RUB",
            prices=[LabeledPrice(label=f"Заказ №{order.id}", amount=amount)],
        )
        return True
    except Exception as e:
        log.warning("invoice failed: %s", e)
        return False


async def send_reserve_invoice(bot, settings, user_id: int, rid: int, title: str, size: str) -> bool:
    if not is_configured(settings):
        return False
    from aiogram.types import LabeledPrice

    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=f"Бронь размера — {title[:40]}",
            description=f"Размер {size or '—'}. Держим 24 часа, 199₽ идут в зачёт заказа.",
            payload=f"res:{rid}",
            provider_token=settings.telegram_payment_provider_token,
            currency="RUB",
            prices=[LabeledPrice(label="Бронь 24ч", amount=19900)],
        )
        return True
    except Exception as e:
        log.warning("reserve invoice failed: %s", e)
        return False


async def send_box_invoice(bot, settings, user_id: int) -> bool:
    if not is_configured(settings):
        return False
    from aiogram.types import LabeledPrice

    try:
        await bot.send_invoice(
            chat_id=user_id,
            title="NORMWEAR — подписка NORM BOX",
            description="3 вещи-сюрприз каждый месяц за 5990₽.",
            payload=f"box:{user_id}",
            provider_token=settings.telegram_payment_provider_token,
            currency="RUB",
            prices=[LabeledPrice(label="NORM BOX / месяц", amount=599000)],
        )
        return True
    except Exception as e:
        log.warning("box invoice failed: %s", e)
        return False
