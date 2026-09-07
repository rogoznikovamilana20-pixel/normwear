import math

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ORDER_NEXT,
    Brand,
    CartItem,
    LoyaltyTransaction,
    Order,
    OrderItem,
    OrderStatusHistory,
    PromoCode,
    PromoRedemption,
    Product,
    Referral,
    User,
    utcnow,
)
from app.services import retention as retention_svc

BONUS_EARN_RATE = 0.01  # базовый уровень, см. retention.earn_rate (уровни 1/3/5%)
BONUS_SPEND_SHARE = 0.30
REFERRAL_REWARD = 100
ACTIVE_ORDER_STATUSES = ("awaiting_delivery", "awaiting_payment", "shipped")


async def get_or_create_user(session: AsyncSession, tg_user, referrer_id: int | None = None) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name)
        if referrer_id and referrer_id != tg_user.id:
            ref = await session.get(User, referrer_id)
            if ref is not None:
                user.referrer_id = referrer_id
                session.add(Referral(referrer_id=referrer_id, referred_id=tg_user.id, reward_points=REFERRAL_REWARD))
        session.add(user)
        try:
            await session.flush()
        except Exception as e:
            # гонка: два запроса одновременно создают одного юзера
            from sqlalchemy.exc import IntegrityError

            if isinstance(e, IntegrityError) or "UNIQUE constraint failed" in str(e):
                await session.rollback()
                user = await session.get(User, tg_user.id)
                if user is None:
                    raise
                user.username = tg_user.username
                user.first_name = tg_user.first_name
                user.last_seen = utcnow()
                await session.flush()
            else:
                raise
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_seen = utcnow()
        await session.flush()
    return user


async def get_cart(session: AsyncSession, user_id: int) -> list:
    return (
        await session.execute(
            select(CartItem, Product)
            .join(Product, CartItem.product_id == Product.id)
            .where(CartItem.user_id == user_id)
            .order_by(CartItem.id)
        )
    ).all()


async def cart_count(session: AsyncSession, user_id: int) -> int:
    return (
        await session.execute(select(func.count()).select_from(CartItem).where(CartItem.user_id == user_id))
    ).scalar() or 0


async def validate_promo(session: AsyncSession, code: str | None, subtotal: float, user_id: int):
    if not code:
        return None, 0.0, None
    promo = (await session.scalars(select(PromoCode).where(PromoCode.code == code.strip().upper()))).first()
    if promo is None or not promo.is_active:
        return None, 0.0, "Промокод не найден"
    if promo.expires_at is not None and promo.expires_at < utcnow():
        return None, 0.0, "Промокод истёк"
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        return None, 0.0, "Лимит промокода исчерпан"
    if subtotal < promo.min_order:
        return None, 0.0, f"Промокод действует от {int(promo.min_order)} ₽"
    discount = subtotal * promo.value / 100.0 if promo.kind == "percent" else min(promo.value, subtotal)
    return promo, round(discount, 2), None


async def preview_totals(session: AsyncSession, user: User, promo_code: str | None, use_bonus: bool) -> dict:
    items = await get_cart(session, user.id)
    subtotal = sum(float(p.retail_price or 0) * ci.qty for ci, p in items)
    _, discount, promo_error = await validate_promo(session, promo_code, subtotal, user.id)
    if promo_error:
        discount = 0.0
    bonus_available = user.bonus_points or 0
    bonus_max = min(bonus_available, math.floor(subtotal * BONUS_SPEND_SHARE)) if subtotal else 0
    bonus_used = bonus_max if use_bonus else 0
    total = max(0.0, subtotal - discount - bonus_used)
    return {
        "items": items,
        "subtotal": round(subtotal, 2),
        "discount": discount,
        "promo_error": promo_error if promo_code else None,
        "bonus_available": bonus_available,
        "bonus_max": bonus_max,
        "bonus_used": bonus_used,
        "total": round(total, 2),
        "bonus_earned": retention_svc.bonus_for_total(total, user.total_spent),
    }


async def create_order(session: AsyncSession, user: User, data: dict):
    totals = await preview_totals(session, user, data.get("promo_code"), bool(data.get("use_bonus")))
    if not totals["items"]:
        return None, "Корзина пуста"
    promo, discount, promo_error = await validate_promo(session, data.get("promo_code"), totals["subtotal"], user.id)
    if promo_error:
        promo = None
        discount = 0.0
    bonus_used = totals["bonus_used"]
    total = max(0.0, totals["subtotal"] - discount - bonus_used)
    order = Order(
        user_id=user.id,
        status="awaiting_delivery",
        subtotal=totals["subtotal"],
        discount=discount,
        total=round(total, 2),
        promo_code=promo.code if promo else None,
        bonus_used=bonus_used,
        bonus_earned=retention_svc.bonus_for_total(total, user.total_spent),
        full_name=(data.get("full_name") or "")[:120],
        phone=(data.get("phone") or "")[:30],
        city=(data.get("city") or "")[:60],
        address=(data.get("address") or "")[:250],
    )
    session.add(order)
    await session.flush()
    for ci, p in totals["items"]:
        brand = await session.get(Brand, p.brand_id) if p.brand_id else None
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=p.id,
                title=p.title,
                brand=brand.title if brand else None,
                size=ci.size,
                price=float(p.retail_price or 0),
                qty=ci.qty,
            )
        )
    session.add(OrderStatusHistory(order_id=order.id, from_status=None, to_status="awaiting_delivery", changed_by=user.id))
    if promo is not None:
        promo.used_count += 1
        session.add(PromoRedemption(promo_id=promo.id, user_id=user.id, order_id=order.id))
    if bonus_used:
        user.bonus_points = max(0, (user.bonus_points or 0) - bonus_used)
        session.add(LoyaltyTransaction(user_id=user.id, order_id=order.id, points=-bonus_used, kind="spend", note=f"Списание за заказ №{order.id}"))
    user.orders_count = (user.orders_count or 0) + 1
    await session.execute(delete(CartItem).where(CartItem.user_id == user.id))
    await session.commit()
    return order, None


async def advance_order(
    session: AsyncSession,
    order_id: int,
    to_status: str | None = None,
    changed_by: int | None = None,
    tracking: str | None = None,
    note: str | None = None,
) -> Order | None:
    order = await session.get(Order, order_id)
    if order is None:
        return None
    to_status = to_status or ORDER_NEXT.get(order.status)
    if not to_status:
        return order
    from_status = order.status
    order.status = to_status
    order.updated_at = utcnow()
    if tracking:
        order.tracking_number = tracking[:64]
    session.add(OrderStatusHistory(order_id=order.id, from_status=from_status, to_status=to_status, changed_by=changed_by, note=note))
    user = await session.get(User, order.user_id)
    if to_status == "completed" and user is not None:
        user.bonus_points = (user.bonus_points or 0) + order.bonus_earned
        user.total_spent = (user.total_spent or 0) + order.total
        session.add(LoyaltyTransaction(user_id=user.id, order_id=order.id, points=order.bonus_earned, kind="earn", note=f"Кэшбэк за заказ №{order.id}"))
        ref = (await session.scalars(select(Referral).where(Referral.referred_id == user.id, Referral.rewarded.is_(False)))).first()
        if ref is not None:
            referrer = await session.get(User, ref.referrer_id)
            if referrer is not None:
                referrer.bonus_points = (referrer.bonus_points or 0) + ref.reward_points
                session.add(LoyaltyTransaction(user_id=referrer.id, points=ref.reward_points, kind="referral", note=f"Друг завершил заказ №{order.id}"))
            ref.rewarded = True
    if to_status == "cancelled" and user is not None and order.bonus_used:
        user.bonus_points = (user.bonus_points or 0) + order.bonus_used
        session.add(LoyaltyTransaction(user_id=user.id, order_id=order.id, points=order.bonus_used, kind="refund", note=f"Возврат бонусов, заказ №{order.id} отменён"))
    await session.commit()
    if to_status == "completed":
        try:
            from app.services import notify as _notify

            await _notify.request_review(order)
        except Exception:
            pass
    return order


async def set_tracking(session: AsyncSession, order_id: int, tracking: str) -> Order | None:
    order = await session.get(Order, order_id)
    if order is None:
        return None
    order.tracking_number = tracking[:64]
    order.updated_at = utcnow()
    await session.commit()
    return order