import asyncio, time
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from .config import settings
from .db import SessionLocal
from .models import Product, Order
from .publisher import ChannelPublisher
import json

_broadcast_last: dict[int, float] = {}

dp = Dispatcher()

def allowed(user_id: int) -> bool:
    return bool(settings.admin_ids) and user_id in settings.admin_ids

@dp.message(Command('start'))
async def start(message: Message):
    if not allowed(message.from_user.id): return
    await message.answer('NORMWEAR ADMIN\n\n/pending — товары на проверке\n/orders — заказы\n/stats — статистика\n/delivery ID COST — задать доставку\n/edit ID PRICE — изменить цену\n/stock ID NUM — изменить остаток\n/product ID — карточка товара\n/broadcast ТЕКСТ — пост в канал')

@dp.message(Command('pending'))
async def pending(message: Message):
    if not allowed(message.from_user.id): return
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Product).where(Product.status == 'pending').order_by(Product.id.desc()).limit(20))).all()
    if not rows: return await message.answer('Нет товаров на проверке.')
    for p in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ Опубликовать', callback_data=f'approve:{p.id}'), InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject:{p.id}')]])
        await message.answer(f'#{p.id} {p.title}\nЗакупка: {float(p.purchase_price):,.0f} ₽\nЦена: {float(p.sale_price):,.0f} ₽\nУверенность: {float(p.price_confidence):.0%}', reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith(('approve:', 'reject:')))
async def decision(call):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    action, raw_id = call.data.split(':', 1)
    async with SessionLocal() as db:
        product = await db.get(Product, int(raw_id))
        if not product: return await call.answer('Товар не найден', show_alert=True)
        if action == 'reject':
            product.status = 'rejected'
            await db.commit(); await call.answer('Отклонён'); return await call.message.edit_reply_markup(reply_markup=None)
        product.status = 'published'
        await db.commit()
        media = json.loads(product.media_json)
    try:
        mid = await ChannelPublisher().publish(product, media)
        async with SessionLocal() as db:
            product = await db.get(Product, int(raw_id)); product.channel_message_id = mid; await db.commit()
        await call.answer('Опубликован')
    except Exception as e:
        async with SessionLocal() as db:
            product = await db.get(Product, int(raw_id)); product.status = 'approved'; await db.commit()
        await call.answer('Ошибка публикации', show_alert=True)
    await call.message.edit_reply_markup(reply_markup=None)

@dp.message(Command('orders'))
async def orders(message: Message):
    if not allowed(message.from_user.id): return
    async with SessionLocal() as db: rows = (await db.scalars(select(Order).order_by(Order.id.desc()).limit(20))).all()
    await message.answer('\n'.join(f'#{o.id} — {o.status} — {float(o.subtotal):,.0f} ₽ — доставка: {float(o.delivery_cost) if o.delivery_cost is not None else "уточняется"}' for o in rows) or 'Заказов нет.')

@dp.message(Command('delivery'))
async def delivery(message: Message):
    if not allowed(message.from_user.id): return
    parts = (message.text or '').split()
    if len(parts) != 3: return await message.answer('Формат: /delivery ORDER_ID COST')
    try: oid, cost = int(parts[1]), float(parts[2])
    except ValueError: return await message.answer('ID и стоимость должны быть числами.')
    async with SessionLocal() as db:
        o = await db.get(Order, oid)
        if not o: return await message.answer('Заказ не найден.')
        o.delivery_cost = cost; o.total = float(o.subtotal) + cost; o.status = 'awaiting_payment'; await db.commit()
    await message.answer(f'Заказ #{oid}: доставка {cost:,.0f} ₽. Итог: {float(o.total):,.0f} ₽. Клиента нужно уведомить/выставить оплату.')

@dp.message(Command('stats'))
async def stats(message: Message):
    if not allowed(message.from_user.id): return
    async with SessionLocal() as db:
        products = await db.scalar(select(func.count(Product.id))) or 0; orders = await db.scalar(select(func.count(Order.id))) or 0; revenue = await db.scalar(select(func.coalesce(func.sum(Order.subtotal),0))) or 0
    await message.answer(f'Товары: {products}\nЗаказы: {orders}\nВыручка по товарам: {float(revenue):,.0f} ₽')

@dp.message(Command('edit'))
async def edit_price(message: Message):
    if not allowed(message.from_user.id): return
    parts=(message.text or '').split()
    if len(parts)!=3: return await message.answer('Формат: /edit PRODUCT_ID NEW_PRICE\nПример: /edit 12 8490')
    try: pid=int(parts[1]); price=float(parts[2])
    except ValueError: return await message.answer('ID и цена — числа.')
    if price<100 or price>500000: return await message.answer('Цена вне диапазона 100-500000 ₽')
    async with SessionLocal() as db:
        p=await db.get(Product, pid)
        if not p: return await message.answer('Товар не найден.')
        old=float(p.sale_price)
        p.sale_price=price
        p.price_confidence=1.0
        await db.commit()
    await message.answer(f'#{pid} цена {old:,.0f} → {price:,.0f} ₽')

@dp.message(Command('stock'))
async def edit_stock(message: Message):
    if not allowed(message.from_user.id): return
    parts=(message.text or '').split()
    if len(parts)!=3: return await message.answer('Формат: /stock PRODUCT_ID NUM\nПример: /stock 12 5')
    try: pid=int(parts[1]); num=int(parts[2])
    except ValueError: return await message.answer('ID и остаток — числа.')
    if num<0 or num>10000: return await message.answer('Остаток 0-10000')
    async with SessionLocal() as db:
        p=await db.get(Product, pid)
        if not p: return await message.answer('Товар не найден.')
        p.stock=num
        await db.commit()
    await message.answer(f'#{pid} остаток → {num}')

@dp.message(Command('product'))
async def product_card(message: Message):
    if not allowed(message.from_user.id): return
    parts=(message.text or '').split()
    if len(parts)!=2: return await message.answer('Формат: /product ID')
    try: pid=int(parts[1])
    except ValueError: return await message.answer('ID — число.')
    async with SessionLocal() as db:
        p=await db.get(Product, pid)
        if not p: return await message.answer('Товар не найден.')
    sizes=", ".join(json.loads(p.sizes_json)) if p.sizes_json else "—"
    await message.answer(f"#{p.id} {p.title}\nСтатус: {p.status}\nЗакупка: {float(p.purchase_price):,.0f} ₽\nЦена: {float(p.sale_price):,.0f} ₽\nОстаток: {p.stock}\nРазмеры: {sizes}\nКатегория: {p.category}\nКанал: {p.channel_message_id or '—'}")

@dp.message(Command('broadcast'))
async def broadcast(message: Message):
    if not allowed(message.from_user.id): return
    # rate limit 1 per 60s per admin
    now = time.time()
    last = _broadcast_last.get(message.from_user.id, 0)
    if now - last < 60:
        return await message.answer(f'Подожди {int(60 - (now-last))}с перед следующим постом.')
    text = (message.text or '').split(maxsplit=1)
    if len(text) < 2 or not text[1].strip():
        return await message.answer('Формат: /broadcast ТЕКСТ\nПоддерживает HTML: <b>жирный</b>, <i>курсив</i>')
    raw = text[1].strip()
    if len(raw) > 4000:
        return await message.answer('Текст слишком длинный (макс 4000).')
    # if reply to photo, send photo+captain else text
    try:
        bot = Bot(settings.shop_bot_token)
        if message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1].file_id
            await bot.send_photo(settings.shop_channel_id, photo, caption=raw, parse_mode='HTML')
        else:
            await bot.send_message(settings.shop_channel_id, raw, parse_mode='HTML')
        await bot.session.close()
        _broadcast_last[message.from_user.id] = now
        await message.answer('✅ Опубликовано в канал.')
    except Exception as e:
        await message.answer(f'Ошибка публикации: {e}')

async def main():
    await dp.start_polling(Bot(settings.admin_bot_token))

if __name__ == '__main__': asyncio.run(main())
