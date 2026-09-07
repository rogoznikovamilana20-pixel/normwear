import asyncio
import logging
import math
from datetime import timedelta

log = logging.getLogger("retention")

# уровни кэшбэка по lifetime-тратам: (порог, ставка)
TIERS = [(0, 0.01), (20000, 0.03), (50000, 0.05)]
TIER_NAMES = {0.01: "Старт 1%", 0.03: "Про 3%", 0.05: "VIP 5%"}


def earn_rate(total_spent: float | None) -> float:
    spent = total_spent or 0
    rate = 0.01
    for threshold, r in TIERS:
        if spent >= threshold:
            rate = r
    return rate


def tier_name(total_spent: float | None) -> str:
    return TIER_NAMES.get(earn_rate(total_spent), "Старт 1%")


async def _personal_promo(session, user_id: int, prefix: str, value: float) -> str:
    from sqlalchemy import select

    from app.models import PromoCode

    code = f"{prefix}{user_id % 1000000:06d}"
    exists = (await session.scalars(select(PromoCode).where(PromoCode.code == code))).first()
    if exists is None:
        session.add(PromoCode(code=code, kind="percent", value=value, min_order=2000, max_uses=1))
        await session.commit()
    return code


async def abandoned_cart_loop() -> None:
    from sqlalchemy import select

    from app.db import SessionMaker
    from app.models import CartItem, RetentionPing, utcnow

    while True:
        try:
            await asyncio.sleep(30 * 60)
            from app.services import notify as notify_svc

            if notify_svc.shop_bot is None:
                continue
            cutoff = utcnow() - timedelta(hours=2)
            async with SessionMaker() as s:
                rows = (
                    await s.execute(
                        select(CartItem.user_id, CartItem.added_at)
                        .where(CartItem.added_at < cutoff)
                        .order_by(CartItem.user_id)
                    )
                ).all()
                seen: set[int] = set()
                for user_id, _added in rows:
                    if user_id in seen:
                        continue
                    seen.add(user_id)
                    last = (
                        await s.scalars(
                            select(RetentionPing)
                            .where(RetentionPing.user_id == user_id, RetentionPing.kind == "cart")
                            .order_by(RetentionPing.id.desc())
                        )
                    ).first()
                    if last and (utcnow() - last.created_at) < timedelta(hours=48):
                        continue
                    code = await _personal_promo(s, user_id, "CART", 5.0)
                    s.add(RetentionPing(user_id=user_id, kind="cart"))
                    await s.commit()
                    try:
                        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                        kb = InlineKeyboardMarkup(
                            inline_keyboard=[[InlineKeyboardButton(text="🛒 Вернуться в корзину", callback_data="cart")]]
                        )
                        await notify_svc.shop_bot.send_message(
                            user_id,
                            f"🛒 Ты забыл корзину! Держи промокод <code>{code}</code> -5% — действует 1 раз.\nЗаглядывай, размеры разбирают.",
                            reply_markup=kb,
                        )
                    except Exception:
                        continue
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("cart loop: %s", e)


async def winback_loop() -> None:
    from sqlalchemy import select

    from app.db import SessionMaker
    from app.models import Order, RetentionPing, User, utcnow

    while True:
        try:
            await asyncio.sleep(24 * 60 * 60)
            from app.services import notify as notify_svc

            if notify_svc.shop_bot is None:
                continue
            cutoff = utcnow() - timedelta(days=30)
            async with SessionMaker() as s:
                users = (await s.scalars(select(User).where(User.last_seen < cutoff, User.orders_count > 0))).all()
                for u in users:
                    last = (
                        await s.scalars(
                            select(RetentionPing)
                            .where(RetentionPing.user_id == u.id, RetentionPing.kind == "winback")
                            .order_by(RetentionPing.id.desc())
                        )
                    ).first()
                    if last and (utcnow() - last.created_at) < timedelta(days=30):
                        continue
                    recent = (
                        await s.scalars(select(Order).where(Order.user_id == u.id).order_by(Order.id.desc()).limit(1))
                    ).first()
                    if recent and recent.created_at >= cutoff:
                        continue
                    code = await _personal_promo(s, u.id, "BACK", 10.0)
                    s.add(RetentionPing(user_id=u.id, kind="winback"))
                    await s.commit()
                    try:
                        await notify_svc.shop_bot.send_message(
                            u.id,
                            f"👋 Давно не виделись! Вот промокод <code>{code}</code> -10% — загляни за новинками.",
                        )
                    except Exception:
                        continue
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("winback loop: %s", e)


async def check_stock_requests(session, product) -> int:
    """Уведомить ждущих размер. Возвращает число уведомлений."""
    from sqlalchemy import select

    from app.models import StockRequest

    from app.services import notify as notify_svc

    sizes = [str(x) for x in (product.sizes or [])]
    if not sizes:
        return 0
    reqs = (
        await session.scalars(
            select(StockRequest).where(
                StockRequest.product_id == product.id, StockRequest.notified == False  # noqa: E712
            )
        )
    ).all()
    n = 0
    for r in reqs:
        if r.size and r.size not in sizes:
            continue
        r.notified = True
        n += 1
        try:
            if notify_svc.shop_bot is not None:
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🛍 Открыть", callback_data=f"pr:{product.id}")]]
                )
                await notify_svc.shop_bot.send_message(
                    r.user_id,
                    f"📩 Размер {r.size or 'твой'} появился: <b>{product.title}</b> — забирай пока не разобрали!",
                    reply_markup=kb,
                )
        except Exception:
            continue
    if n:
        await session.commit()
    return n


def bonus_for_total(total: float, total_spent: float | None) -> int:
    return math.floor(max(0.0, total) * earn_rate(total_spent))
