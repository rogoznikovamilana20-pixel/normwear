from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import SessionMaker
from app.bots.shop import main_menu, menu_inline, router

FAQ = {
    "delivery": (
        "🚚 Доставка",
        "Доставка 2–4 дня по России.\n"
        "Каждый товар проверяем перед отправкой.\n"
        "Трек-номер придёт сюда автоматически + статус виден в «📦 Мои заказы».",
    ),
    "payment": (
        "💳 Оплата",
        "После оформления менеджер пришлёт реквизиты СБП/карты в этот чат.\n"
        "Пришли чек — заказ сразу поедет.\n"
        "Бонусами можно оплатить до 30% заказа.",
    ),
    "sizes": (
        "📏 Размеры",
        "Все размеры — в карточке товара.\n"
        "Нет твоего? Жми «📩 Нет моего размера» — пришлём пуш когда появится.",
    ),
    "return": (
        "↩️ Возврат и обмен",
        "Не подошёл размер или брак — напиши менеджеру в «📞 Поддержка» в течение 14 дней.\n"
        "Решим обмен или возврат, без бюрократии.",
    ),
    "bonus": (
        "🎁 Бонусы и промокоды",
        "Кэшбэк с каждой покупки: Старт 1% → Про 3% (выкуп от 20к) → VIP 5% (от 50к).\n"
        "Промокод вводится при оформлении. Колесо фортуны — каждый день в «🎡 Колесо».",
    ),
    "track": (
        "📮 Где мой заказ",
        "Статус смотри в «📦 Мои заказы» или мини-аппе.\n"
        "Этапы: принят → готов к оплате → отправлен (трек) → доставлен → завершён.",
    ),
}

# слово -> ключ (порядок важен)
KEYWORDS = [
    ("возврат", "return"), ("обмен", "return"), ("верну", "return"),
    ("трек", "track"), ("посылк", "track"), ("где заказ", "track"), ("где мой", "track"),
    ("размер", "sizes"), ("сетк", "sizes"), ("маломер", "sizes"),
    ("бонус", "bonus"), ("кэшбэк", "bonus"), ("кешбэк", "bonus"), ("промокод", "bonus"), ("промо", "bonus"), ("скидк", "bonus"),
    ("оплат", "payment"), ("реквизит", "payment"), ("сбп", "payment"), ("счет", "payment"), ("счёт", "payment"),
    ("доставк", "delivery"), ("отправк", "delivery"), ("сколько ждать", "delivery"), ("когда придет", "delivery"), ("когда придёт", "delivery"),
]

pending_faq: dict[int, str] = {}


def faq_list_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f"faq:{key}")] for key, (title, _) in FAQ.items()]
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_faq(target: Message):
    await target.answer("❓ <b>Частые вопросы</b>\nВыбери тему:", reply_markup=faq_list_kb())


def match_faq(text: str) -> str | None:
    t = (text or "").lower()
    for kw, key in KEYWORDS:
        if kw in t:
            return key
    return None


async def send_faq_answer(target: Message, key: str, with_human: bool = True):
    title, body = FAQ[key]
    rows = []
    if with_human:
        rows.append([InlineKeyboardButton(text="💬 Всё равно спросить менеджера", callback_data="faq_human")])
    rows.append([InlineKeyboardButton(text="❓ Все вопросы", callback_data="m_faq")])
    await target.answer(f"<b>{title}</b>\n\n{body}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("faq"))
async def cmd_faq(message: Message):
    await send_faq(message)


@router.callback_query(F.data == "m_faq")
async def cb_m_faq(cb: CallbackQuery):
    await send_faq(cb.message)
    await cb.answer()


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq_item(cb: CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in FAQ:
        await cb.answer()
        return
    title, body = FAQ[key]
    try:
        await cb.message.edit_text(
            f"<b>{title}</b>\n\n{body}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❓ Все вопросы", callback_data="m_faq")]]
            ),
        )
    except Exception:
        await cb.message.answer(f"<b>{title}</b>\n\n{body}")
    await cb.answer()


@router.callback_query(F.data == "faq_human")
async def cb_faq_human(cb: CallbackQuery):
    from sqlalchemy import select

    from app.models import SupportMessage, SupportTicket, utcnow

    text = pending_faq.pop(cb.from_user.id, "")
    if not text:
        await cb.message.answer("✍️ Напиши вопрос одним сообщением — менеджер ответит прямо в этот чат.")
        await cb.answer()
        return
    async with SessionMaker() as s:
        ticket = (
            await s.scalars(
                select(SupportTicket)
                .where(SupportTicket.user_id == cb.from_user.id, SupportTicket.status == "open")
                .order_by(SupportTicket.id.desc())
            )
        ).first()
        if ticket is None:
            ticket = SupportTicket(user_id=cb.from_user.id)
            s.add(ticket)
            await s.flush()
        s.add(SupportMessage(ticket_id=ticket.id, from_admin=False, text=text[:3000]))
        ticket.updated_at = utcnow()
        await s.commit()
        ticket_id = ticket.id
    import html as _html

    from app.services import notify as notify_svc

    uname = f"@{cb.from_user.username}" if cb.from_user.username else "без юзернейма"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"tk:{cb.from_user.id}")]])
    await notify_svc.notify_admins(
        f"💬 <b>Сообщение от клиента</b>\n"
        f"👤 {_html.escape(cb.from_user.first_name or '')} {uname} · id <code>{cb.from_user.id}</code> · тикет #{ticket_id}\n\n"
        f"{_html.escape(text[:1000])}",
        kb,
    )
    await cb.message.answer("Передал менеджеру ✅ Ответ придёт в этот чат.")
    await cb.answer()
