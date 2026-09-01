import asyncio
import json
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import Product, Order

dp = Dispatcher()

def _miniapp_url(product_id: int | None = None) -> str:
    base = settings.miniapp_url_template.split('?')[0]
    if product_id is None:
        return base
    try:
        return settings.miniapp_url_template.format(product_id=product_id)
    except Exception:
        return f"{base}?product={product_id}"

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🛍 Открыть магазин', web_app=WebAppInfo(url=_miniapp_url()))],
        [InlineKeyboardButton(text='📦 Каталог', callback_data='catalog:0'),
         InlineKeyboardButton(text='📋 Мои заказы', callback_data='myorders:0')],
        [InlineKeyboardButton(text='💬 Поддержка', callback_data='support')]
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')]
    ])

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        'Добро пожаловать в <b>NORMWEAR</b>.\n\n'
        'Вся витрина и заказ — внутри Telegram.',
        parse_mode='HTML',
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == 'back:main')
async def back_main(call: CallbackQuery):
    await call.message.edit_text(
        'Добро пожаловать в <b>NORMWEAR</b>.\n\n'
        'Вся витрина и заказ — внутри Telegram.',
        parse_mode='HTML',
        reply_markup=main_menu()
    )
    await call.answer()

@dp.callback_query(F.data == 'support')
async def support_cb(call: CallbackQuery):
    await call.message.edit_text(
        '💬 <b>Поддержка NORMWEAR</b>\n\n'
        'Напишите ваш вопрос сюда — менеджер ответит в ближайшее время.',
        parse_mode='HTML',
        reply_markup=back_menu()
    )
    await call.answer()

# ── ЛЮБОЕ ТЕКСТОВОЕ СООБЩЕНИЕ ──

@dp.message(F.text)
async def on_text(message: Message):
    text = message.text.strip()

    # попробовать как номер заказа
    if text.isdigit():
        oid = int(text)
        async with SessionLocal() as db:
            o = await db.scalar(select(Order).where(Order.id == oid, Order.telegram_user_id == message.from_user.id))
        if o:
            delivery = f"{float(o.delivery_cost):,.0f} ₽" if o.delivery_cost is not None else "уточняется"
            total = f"{float(o.total):,.0f} ₽" if o.total is not None else "—"
            status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚'}.get(o.status, '📋')
            await message.answer(
                f'{status_emoji} <b>Заказ #{o.id}</b>\n'
                f'Статус: {o.status}\n'
                f'Товары: {float(o.subtotal):,.0f} ₽\n'
                f'Доставка: {delivery}\n'
                f'Итого: {total}',
                parse_mode='HTML',
                reply_markup=back_menu()
            )
            return

    # попробовать как поиск по каталогу
    async with SessionLocal() as db:
        like = f"%{text}%"
        rows = (await db.scalars(
            select(Product).where(Product.status == 'published', Product.stock > 0, Product.title.ilike(like))
            .order_by(Product.created_at.desc()).limit(5)
        )).all()
    if rows:
        lines = []
        kb_rows = []
        for p in rows:
            sizes = ", ".join(json.loads(p.sizes_json)) if p.sizes_json else "—"
            lines.append(f"#{p.id} <b>{p.title}</b> — {float(p.sale_price):,.0f} ₽\nРазмеры: {sizes}")
            kb_rows.append([InlineKeyboardButton(text=f"🛍 {p.title[:30]} — {float(p.sale_price):,.0f} ₽", web_app=WebAppInfo(url=_miniapp_url(p.id)))])
        kb_rows.append([InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')])
        await message.answer(
            f'🔍 По запросу «{text}» нашлось:\n\n' + '\n\n'.join(lines),
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
        return

    # ничего не нашлось — показать меню
    await message.answer(
        'Не нашёл такого. Вот что я умею:',
        reply_markup=main_menu()
    )

# ── СТИКЕРЫ / ФОТО / ПРОЧЕЕ ──

@dp.message(F.sticker | F.photo | F.animation | F.voice | F.video)
async def on_media(message: Message):
    await message.answer('Принимаю только текст. Вот меню:', reply_markup=main_menu())

# ── КАТАЛОГ ──

async def _send_catalog(target: Message | CallbackQuery, page: int):
    is_callback = isinstance(target, CallbackQuery)
    limit = 5
    offset = page * limit
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Product).where(Product.status=='published', Product.stock>0).order_by(Product.created_at.desc()).limit(limit).offset(offset))).all()
    if not rows:
        text = 'Каталог пуст.'
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')]
        ]) if page > 0 else back_menu()
        if is_callback:
            await target.message.edit_text(text, reply_markup=kb)
            await target.answer()
        else:
            await target.answer(text, reply_markup=kb)
        return
    lines = []
    kb_rows = []
    for p in rows:
        sizes = ", ".join(json.loads(p.sizes_json)) if p.sizes_json else "—"
        lines.append(f"#{p.id} <b>{p.title}</b> — {float(p.sale_price):,.0f} ₽\nРазмеры: {sizes}")
        kb_rows.append([InlineKeyboardButton(text=f"🛍 {p.title[:30]} — {float(p.sale_price):,.0f} ₽", web_app=WebAppInfo(url=_miniapp_url(p.id)))])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'catalog:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    async with SessionLocal() as db:
        nxt = (await db.scalars(select(Product).where(Product.status=='published', Product.stock>0).order_by(Product.created_at.desc()).limit(1).offset(offset+limit))).first()
    if nxt:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'catalog:{page+1}'))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')])
    text = "📦 <b>Каталог NORMWEAR</b>\n\n" + "\n\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    if is_callback:
        try:
            await target.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception:
            await target.message.answer(text, parse_mode='HTML', reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, parse_mode='HTML', reply_markup=kb)

@dp.callback_query(F.data.startswith('catalog:'))
async def catalog_page(call: CallbackQuery):
    try:
        page = int(call.data.split(':',1)[1])
    except Exception:
        page = 0
    await _send_catalog(call, page)

# ── ЗАКАЗЫ ──

async def _send_myorders(target: Message | CallbackQuery, page: int = 0):
    is_cb = isinstance(target, CallbackQuery)
    user_id = target.from_user.id
    limit = 5
    offset = page * limit
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Order).where(Order.telegram_user_id==user_id).order_by(Order.id.desc()).limit(limit).offset(offset))).all()
        nxt = (await db.scalars(select(Order).where(Order.telegram_user_id==user_id).order_by(Order.id.desc()).limit(1).offset(offset+limit))).first()
    if not rows and page == 0:
        text = 'У вас пока нет заказов.\n\nОткройте каталог чтобы выбрать товар.'
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📦 Каталог', callback_data='catalog:0')],
            [InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')]
        ])
        if is_cb:
            await target.message.edit_text(text, reply_markup=kb)
            await target.answer()
        else:
            await target.answer(text, reply_markup=kb)
        return
    if not rows:
        if is_cb:
            await target.answer('Больше заказов нет', show_alert=True)
        return
    lines = []
    for o in rows:
        status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚'}.get(o.status, '📋')
        delivery = f"{float(o.delivery_cost):,.0f} ₽" if o.delivery_cost is not None else "уточняется"
        total = f"{float(o.total):,.0f} ₽" if o.total is not None else "—"
        lines.append(f"{status_emoji} #{o.id} — товары {float(o.subtotal):,.0f} ₽\nДоставка: {delivery} — Итого: {total}")
    text = f"📋 <b>Ваши заказы</b>\n\n" + "\n\n".join(lines)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'myorders:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    if nxt:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'myorders:{page+1}'))
    kb_rows = [nav, [InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')]]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    if is_cb:
        try:
            await target.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception:
            await target.message.answer(text, parse_mode='HTML', reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, parse_mode='HTML', reply_markup=kb)

@dp.callback_query(F.data.startswith('myorders:'))
async def myorders_cb(call: CallbackQuery):
    try:
        page = int(call.data.split(':',1)[1])
    except Exception:
        page = 0
    await _send_myorders(call, page)

@dp.callback_query(F.data == 'noop')
async def noop(call: CallbackQuery):
    await call.answer()

async def main():
    bot = Bot(settings.shop_bot_token)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
