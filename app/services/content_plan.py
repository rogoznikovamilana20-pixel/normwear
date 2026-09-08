import asyncio
import logging
from datetime import datetime

log = logging.getLogger("content")

BOT = "https://t.me/norm_shop_bot"

# 30 тем на месяц: 1 пост в день в 12:00 МСК
PLAN = [
    "🔥 <b>Новые дропы уже в каталоге</b>\n\nBAPE, Corteiz, Nike — разбирают быстро. Заходи первым.",
    "🎡 <b>Крутил сегодня колесо?</b>\n\nСкидки до 20% и +100 бонусов каждый день. 10 секунд — и приз твой.",
    "🎁 <b>WELCOME10 −10% на первый заказ</b>\n\nВставь промокод при оформлении. Действует на чек от 3000₽.",
    "👔 <b>Хитрость: комплект −10%</b>\n\nКидай в корзину вещи из 2 разных категорий — скидка посчитается сама.",
    "🔒 <b>Боишься что размер уведут?</b>\n\nБронь за 199₽ держит размер 24 часа и идёт в зачёт заказа.",
    "⭐️ <b>Твой отзыв = +50 бонусов</b>\n\nОставь фото-отзыв после заказа и попади в канал.",
    "🔔 <b>Подпишись на бренд</b>\n\nBAPE, Corteiz, Nike — пуш о новинках твоего бренда первым.",
    "💳 <b>Как оплатить</b>\n\nМенеджер пришлёт СБП после подтверждения. Чек — и заказ едет.",
    "🚚 <b>Доставка 2–4 дня</b>\n\nПроверяем каждую вещь перед отправкой. Трек приходит автоматически.",
    "📏 <b>Не знаешь размер?</b>\n\nВсе размеры в карточке. Нет твоего — жми «сообщить», позовём когда появится.",
    "👥 <b>Приведи друга — +100</b>\n\nТвоя ссылка в разделе Бонусы. Друг заказывает — ты богатеешь.",
    "🏆 <b>Уровни кэшбэка</b>\n\nСтарт 1% → Про 3% (выкуп от 20к) → VIP 5% (от 50к). Расти — выгодно.",
    "📦 <b>NORM BOX за 5990₽</b>\n\n3 вещи-сюрприз каждый месяц. Подписка в боте, раздел BOX.",
    "👗 <b>AI-стилист</b>\n\nКинь фото лука в бот — подберём похожее из каталога.",
    "🎙 <b>Голосовой поиск</b>\n\nНаговори «найди худи до десяти тысяч» — найдём.",
    "✨ <b>Мини-каталог</b>\n\nВесь магазин прямо в телеге: поиск, корзина, оформление. Жми Menu слева от ввода.",
    "📉 <b>Уценки</b>\n\nДобавляй в избранное 🤍 — о падении цены сообщим первыми.",
    "↩️ <b>Не подошло?</b>\n\nОбмен или возврат за 14 дней без бюрократии. Пиши в поддержку.",
    "⏰ <b>Дроп-таймеры</b>\n\nПеред жирными поступлениями кидаем обратный отсчёт. Жми «напомнить».",
    "❓ <b>Частые вопросы</b>\n\nКоманда /faq в боте: доставка, оплата, размеры, возврат.",
    "🔥 <b>Топ недели: BAPE</b>\n\nСамые разбираемые футболки и худи. Успей свой размер.",
    "🔥 <b>Топ недели: Corteiz</b>\n\nКарго и лонгсливы. Ходовой товар — не тяни.",
    "🔥 <b>Топ недели: Nike</b>\n\nДанки и кампусы в наличии. Размеры улетают.",
    "💰 <b>Как копить бонусы быстрее</b>\n\nКолесо каждый день + отзывы + друзья. До 30% заказа — бонусами.",
    "📸 <b>Пришли лук</b>\n\nВыложим лучшие фото клиентов в канал. Слава + бонусы.",
    "🛒 <b>Забыл корзину?</b>\n\nМы напомним сами и дадим −5%. Проверь корзину в боте.",
    "🎯 <b>Понедельник = новый дроп</b>\n\nНачинай неделю с обновления гардероба.",
    "💪 <b>Середина недели — время заказать</b>\n\nЧтобы к выходным уже носить новое.",
    "🎉 <b>Пятница: балуй себя</b>\n\nОформи сегодня — менеджер подтвердит за час.",
    "🌙 <b>Воскресный разбор</b>\n\nПолистай каталог под чай — неделя начнётся стильно.",
]

BUTTONS_TEXT = "👇 Каталог и колесо — по кнопкам:"


async def post_next(settings, force: bool = False) -> int | None:
    """Публикует следующий пост плана. Возвращает message_id."""
    from sqlalchemy import func, select

    from app.db import SessionMaker
    from app.models import ChannelPost

    from app.services import notify as notify_svc

    bot = notify_svc.admin_bot
    if bot is None or not settings.shop_channel_id:
        return None
    async with SessionMaker() as s:
        sent = (await s.execute(select(func.count()).select_from(ChannelPost).where(ChannelPost.product_id.is_(None)))).scalar() or 0
        item = PLAN[sent % len(PLAN)]
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Открыть в боте", url=f"{BOT}?start=catalog")],
                [InlineKeyboardButton(text="🎡 Колесо", url=f"{BOT}?start=wheel")],
            ]
        )
        try:
            msg = await bot.send_message(chat_id=settings.shop_channel_id, text=item + f"\n\n{BUTTONS_TEXT}", reply_markup=kb)
        except Exception as e:
            log.warning("content post failed: %s", e)
            return None
        s.add(ChannelPost(product_id=None, message_id=msg.message_id, channel_id=str(settings.shop_channel_id), text=item))
        await s.commit()
        log.info("content posted #%d msg=%d", sent + 1, msg.message_id)
        return msg.message_id


async def content_loop(settings) -> None:
    posted_today: str = ""
    while True:
        try:
            await asyncio.sleep(10 * 60)
            now = datetime.utcnow()
            # 09:00 UTC = 12:00 МСК, один пост в день
            if now.hour >= 9 and posted_today != now.strftime("%Y-%m-%d"):
                mid = await post_next(settings)
                if mid:
                    posted_today = now.strftime("%Y-%m-%d")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("content loop: %s", e)
