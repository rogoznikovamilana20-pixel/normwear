import asyncio
import json
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
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

@dp.message(CommandStart())
async def start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🛍 Открыть NORMWEAR', web_app=WebAppInfo(url=_miniapp_url()))],
        [InlineKeyboardButton(text='📦 Каталог', callback_data='catalog:0'), InlineKeyboardButton(text='📋 Мои заказы', callback_data='myorders:0')],
        [InlineKeyboardButton(text='💬 Поддержка', callback_data='support')]
    ])
    await message.answer(
        'Добро пожаловать в NORMWEAR.\n\n'
        'Вся витрина и заказ — внутри Telegram.\n'
        'Команды: /catalog — каталог, /myorders — заказы, /help — помощь',
        reply_markup=kb
    )

@dp.message(Command('catalog'))
async def catalog_cmd(message: Message):
    await _send_catalog(message, 0)

@dp.message(Command('myorders'))
async def myorders_cmd(message: Message):
    await _send_myorders(message)

@dp.message(Command('help'))
async def help_cmd(message: Message):
    await message.answer(
        'NORMWEAR — помощь\n\n'
        '/catalog — посмотреть товары\n'
        '/myorders — ваши заказы\n'
        '/paysupport — вопросы по оплате\n\n'
        'Оформление — через Mini App (кнопка ниже). Доставку рассчитывает менеджер.\n'
        'Поддержка: напишите сюда, менеджер ответит.'
    )

@dp.message(F.text == '/paysupport')
async def pay_support(message: Message):
    await message.answer('По вопросам оплаты напишите менеджеру магазина. /help — справка.')

async def _send_catalog(target: Message | CallbackQuery, page: int):
    is_callback = isinstance(target, CallbackQuery)
    limit = 5
    offset = page * limit
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Product).where(Product.status=='published', Product.stock>0).order_by(Product.created_at.desc()).limit(limit).offset(offset))).all()
        total = await db.scalar(select(Product).where(Product.status=='published', Product.stock>0).order_by(Product.created_at.desc()).limit(1000).offset(0).count()) if False else None
    if not rows:
        text = 'Каталог пуст или страница пуста.'
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='catalog:0')]]) if page>0 else None
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
        lines.append(f"#{p.id} <b>{p.title}</b> — {float(p.sale_price):,.0f} ₽ (размеры: {sizes})")
        kb_rows.append([InlineKeyboardButton(text=f"🛍 {p.title[:30]} — {float(p.sale_price):,.0f} ₽", web_app=WebAppInfo(url=_miniapp_url(p.id)))])
    # pagination
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'catalog:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    # check if there is next page
    async with SessionLocal() as db:
        nxt = (await db.scalars(select(Product).where(Product.status=='published', Product.stock>0).order_by(Product.created_at.desc()).limit(1).offset(offset+limit))).first()
    if nxt:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'catalog:{page+1}'))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text='🛍 Открыть магазин', web_app=WebAppInfo(url=_miniapp_url()))])
    text = "📦 <b>Каталог NORMWEAR</b>\n\n" + "\n\n".join(lines) + f"\n\nСтраница {page+1}"
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    if is_callback:
        try:
            await target.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception:
            await target.message.answer(text, parse_mode='HTML', reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, parse_mode='HTML', reply_markup=kb)

async def _send_myorders(target: Message | CallbackQuery, page: int = 0):
    is_cb = isinstance(target, CallbackQuery)
    user_id = target.from_user.id if is_cb else target.from_user.id
    msg_obj = target.message if is_cb else target
    limit = 5
    offset = page * limit
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Order).where(Order.telegram_user_id==user_id).order_by(Order.id.desc()).limit(limit).offset(offset))).all()
        # check next
        nxt = (await db.scalars(select(Order).where(Order.telegram_user_id==user_id).order_by(Order.id.desc()).limit(1).offset(offset+limit))).first()
    if not rows and page==0:
        text = 'У вас пока нет заказов. /catalog — выбрать товар.'
        if is_cb:
            await target.message.edit_text(text)
            await target.answer()
        else:
            await target.answer(text)
        return
    if not rows:
        if is_cb:
            await target.answer('Больше заказов нет', show_alert=True)
        return
    lines = []
    for o in rows:
        delivery = f"{float(o.delivery_cost):,.0f} ₽" if o.delivery_cost is not None else "уточняется"
        total = f"{float(o.total):,.0f} ₽" if o.total is not None else "—"
        lines.append(f"#{o.id} — {o.status} — товары {float(o.subtotal):,.0f} ₽ — доставка {delivery} — итог {total}")
    text = f"📋 <b>Ваши заказы</b> (стр. {page+1})\n\n" + "\n".join(lines)
    nav = []
    if page>0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'myorders:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    if nxt:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'myorders:{page+1}'))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav]) if nav else None
    if is_cb:
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

@dp.callback_query(F.data.startswith('myorders:'))
async def myorders_cb(call: CallbackQuery):
    try:
        page = int(call.data.split(':',1)[1])
    except Exception:
        page = 0
    await _send_myorders(call, page)

@dp.callback_query(F.data == 'support')
async def support_cb(call: CallbackQuery):
    await call.answer()
    await call.message.answer('💬 Поддержка NORMWEAR\n\nНапишите ваш вопрос сюда — менеджер ответит в ближайшее время.\n/help — справка.')

@dp.callback_query(F.data == 'noop')
async def noop(call: CallbackQuery):
    await call.answer()

async def main():
    bot = Bot(settings.shop_bot_token)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
