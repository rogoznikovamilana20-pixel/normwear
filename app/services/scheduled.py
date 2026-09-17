"""Отложенные посты в канал: проверка каждую минуту, просроченные уходят при старте."""
import asyncio
import logging

from sqlalchemy import select

from app.models import ScheduledPost, utcnow

log = logging.getLogger("scheduled")


async def pending(s) -> list[ScheduledPost]:
    return (
        await s.scalars(
            select(ScheduledPost).where(ScheduledPost.is_sent == False).order_by(ScheduledPost.send_at)  # noqa: E712
        )
    ).all()


async def send_due(settings) -> int:
    from app.db import SessionMaker
    from app.services import notify as notify_svc

    bot = notify_svc.admin_bot
    if bot is None or not settings.shop_channel_id:
        return 0
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    sent = 0
    async with SessionMaker() as s:
        due = (
            await s.scalars(
                select(ScheduledPost).where(ScheduledPost.is_sent == False, ScheduledPost.send_at <= utcnow())  # noqa: E712
            )
        ).all()
        for post in due:
            kb = None
            if post.buttons:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=str(r[0])[:64], url=str(r[1]))] for r in post.buttons if len(r) >= 2]
                )
            try:
                msg = await bot.send_message(chat_id=settings.shop_channel_id, text=post.text, reply_markup=kb)
            except Exception as e:
                log.warning("scheduled post #%d failed: %s", post.id, e)
                continue
            post.is_sent = True
            post.sent_message_id = msg.message_id
            sent += 1
        await s.commit()
    if sent:
        log.info("scheduled sent: %d", sent)
    return sent


async def scheduled_loop(settings) -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await send_due(settings)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("scheduled loop: %s", e)
