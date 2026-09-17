"""Розыгрыши: участие по диплинку, проверка подписки, рефералы, финал с купонами."""
import logging
import random
import string

from sqlalchemy import func, select

from app.models import Giveaway, GiveawayEntry, GiveawayReferral, PromoCode

log = logging.getLogger("giveaway")

SECOND = ("GIVE2", 15.0, 3000.0)  # code-prefix, percent, min_order
THIRD = ("GIVE3", 10.0, 2000.0)


async def active(s, code: str = "give1") -> Giveaway | None:
    return (
        await s.scalars(select(Giveaway).where(Giveaway.code == code, Giveaway.status == "active"))
    ).first()


async def enter(s, gw: Giveaway, user_id: int) -> bool:
    """True если участник новый."""
    has = (
        await s.scalars(
            select(GiveawayEntry).where(GiveawayEntry.giveaway_id == gw.id, GiveawayEntry.user_id == user_id)
        )
    ).first()
    if has is not None:
        return False
    s.add(GiveawayEntry(giveaway_id=gw.id, user_id=user_id))
    await s.commit()
    return True


async def is_subscribed(bot, channel_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(channel_id, user_id)
        if m.status in ("member", "administrator", "creator"):
            return True
        return bool(m.status == "restricted" and getattr(m, "is_member", False))
    except Exception:
        return False


async def record_referral(s, gw: Giveaway, inviter_id: int, invited_id: int) -> bool:
    """Фиксирует приглашение. False если сам себя / уже зафиксировано."""
    if not inviter_id or inviter_id == invited_id:
        return False
    has = (
        await s.scalars(
            select(GiveawayReferral).where(
                GiveawayReferral.giveaway_id == gw.id, GiveawayReferral.invited_id == invited_id
            )
        )
    ).first()
    if has is not None:
        return False
    s.add(GiveawayReferral(giveaway_id=gw.id, inviter_id=inviter_id, invited_id=invited_id))
    await s.commit()
    return True


async def invite_count(s, gw: Giveaway, user_id: int) -> int:
    return (
        await s.execute(
            select(func.count())
            .select_from(GiveawayReferral)
            .where(GiveawayReferral.giveaway_id == gw.id, GiveawayReferral.inviter_id == user_id)
        )
    ).scalar() or 0


def _gen_code(s, prefix: str) -> str:
    for _ in range(20):
        code = prefix + "-" + "".join(random.choices(string.ascii_uppercase + "23456789", k=4))
        if s.scalar(select(PromoCode.id).where(PromoCode.code == code)) is None:
            return code
    return prefix + "-" + "".join(random.choices(string.ascii_uppercase + "23456789", k=8))


async def draw(s, shop_bot, settings, gw: Giveaway) -> list[tuple[int, str, str | None]]:
    """Финал: [(user_id, приз, код_купона|None)]. Взвешенно: 1 билет + по одному за друга."""
    entries = (await s.scalars(select(GiveawayEntry).where(GiveawayEntry.giveaway_id == gw.id))).all()
    pool = []
    for e in entries:
        if shop_bot is not None and not await is_subscribed(shop_bot, settings.shop_channel_id, e.user_id):
            continue
        pool.append(e.user_id)
    counts = dict(
        (
            await s.execute(
                select(GiveawayReferral.inviter_id, func.count())
                .where(GiveawayReferral.giveaway_id == gw.id)
                .group_by(GiveawayReferral.inviter_id)
            )
        ).all()
    )
    bag = {uid: 1 + int(counts.get(uid, 0)) for uid in pool}
    winners = []
    for _ in range(min(3, len(bag))):
        uids = list(bag)
        pick = random.choices(uids, weights=[bag[u] for u in uids], k=1)[0]
        winners.append(pick)
        del bag[pick]
    out = []
    prizes = [("вещь", None), (f"купон −{SECOND[1]:g}% от {int(SECOND[2])}₽", SECOND), (f"купон −{THIRD[1]:g}% от {int(THIRD[2])}₽", THIRD)]
    for i, uid in enumerate(winners):
        label, spec = prizes[i]
        code = None
        if spec is not None:
            code = _gen_code(s, spec[0])
            s.add(PromoCode(code=code, kind="percent", value=spec[1], min_order=spec[2], max_uses=1))
        out.append((uid, label, code))
    gw.status = "drawn"
    await s.commit()
    return out
