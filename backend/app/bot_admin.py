import asyncio, time
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from .config import settings
from .db import SessionLocal
from .models import Product, Order, BannedProduct
from .publisher import ChannelPublisher
import json

_broadcast_last: dict[int, float] = {}
_user_state: dict[int, str] = {}

dp = Dispatcher()

def allowed(user_id: int) -> bool:
    return bool(settings.admin_ids) and user_id in settings.admin_ids

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📦 Товары', callback_data='menu:products'),
         InlineKeyboardButton(text='📋 Заказы', callback_data='menu:orders')],
        [InlineKeyboardButton(text='📊 Статистика', callback_data='menu:stats'),
         InlineKeyboardButton(text='📢 Рассылка', callback_data='menu:broadcast')],
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')]
    ])

@dp.message(Command('start'))
async def start(message: Message):
    if not allowed(message.from_user.id): return
    await message.answer('NORMWEAR ADMIN', reply_markup=main_menu())

@dp.callback_query(lambda c: c.data == 'back:main')
async def back_main(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await call.message.edit_text('NORMWEAR ADMIN', reply_markup=main_menu())
    await call.answer()

# ── ПРОДУКТЫ ──

@dp.callback_query(lambda c: c.data == 'menu:products')
async def menu_products(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Product).where(Product.status == 'pending').order_by(Product.id.desc()).limit(20))).all()
    if not rows:
        await call.message.edit_text('Нет товаров на проверке ✅', reply_markup=back_menu())
        return await call.answer()
    kb_rows = []
    for p in rows:
        kb_rows.append([InlineKeyboardButton(text=f'#{p.id} {p.title[:30]}', callback_data=f'prod:{p.id}')])
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')])
    await call.message.edit_text(f'📦 Товары на проверке ({len(rows)}):', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('prod:'))
async def product_detail(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
    if not p:
        await call.answer('Товар не найден', show_alert=True)
        return
    sizes = ", ".join(json.loads(p.sizes_json)) if json.loads(p.sizes_json) else "—"
    text = (f'📦 #{p.id} {p.title}\n'
            f'Закупка: {float(p.purchase_price):,.0f} ₽\n'
            f'Цена: {float(p.sale_price):,.0f} ₽\n'
            f'Уверенность: {float(p.price_confidence):.0%}\n'
            f'Остаток: {p.stock}\n'
            f'Размеры: {sizes}\n'
            f'Категория: {p.category}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Опубликовать', callback_data=f'approve:{p.id}'),
         InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject:{p.id}')],
        [InlineKeyboardButton(text='🚫 Забанить', callback_data=f'ban:{p.id}')],
        [InlineKeyboardButton(text='✏️ Цена', callback_data=f'setprice:{p.id}'),
         InlineKeyboardButton(text='📦 Остаток', callback_data=f'setstock:{p.id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:products')],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('approve:'))
async def approve(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p: return await call.answer('Товар не найден', show_alert=True)
        p.status = 'published'
        await db.commit()
        media = json.loads(p.media_json)
    try:
        mid = await ChannelPublisher().publish(p, media)
        async with SessionLocal() as db:
            p = await db.get(Product, pid); p.channel_message_id = mid; await db.commit()
        await call.answer('✅ Опубликован')
    except Exception:
        async with SessionLocal() as db:
            p = await db.get(Product, pid); p.status = 'approved'; await db.commit()
        await call.answer('Ошибка публикации', show_alert=True)
    await call.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(lambda c: c.data and c.data.startswith('reject:'))
async def reject(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p: return await call.answer('Товар не найден', show_alert=True)
        p.status = 'rejected'
        await db.commit()
    await call.answer('❌ Отклонён')
    await call.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(lambda c: c.data and c.data.startswith('ban:'))
async def ban(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p: return await call.answer('Товар не найден', show_alert=True)
        p.status = 'banned'
        banned = BannedProduct(sku=p.sku, title_pattern=p.title[:50], reason='Забанен админом')
        db.add(banned)
        await db.commit()
    await call.answer('🚫 Забанен')
    await call.message.edit_reply_markup(reply_markup=None)

# ── ИЗМЕНИТЬ ЦЕНУ ──

@dp.callback_query(lambda c: c.data and c.data.startswith('setprice:'))
async def set_price_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    _user_state[call.from_user.id] = f'awaiting_price:{pid}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'prod:{pid}')]
    ])
    await call.message.edit_text(f'✏️ Введи новую цену для #{pid} (число):', reply_markup=kb)
    await call.answer()

# ── ИЗМЕНИТЬ ОСТАТОК ──

@dp.callback_query(lambda c: c.data and c.data.startswith('setstock:'))
async def set_stock_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[1])
    _user_state[call.from_user.id] = f'awaiting_stock:{pid}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'prod:{pid}')]
    ])
    await call.message.edit_text(f'📦 Введи новый остаток для #{pid} (число):', reply_markup=kb)
    await call.answer()

# ── ОБРАБОТКА ВВОДА (цена, остаток, доставка, рассылка) ──

@dp.message()
async def text_input(message: Message):
    if not allowed(message.from_user.id): return
    uid = message.from_user.id
    state = _user_state.get(uid)
    if not state: return

    if state.startswith('awaiting_price:'):
        pid = int(state.split(':')[1])
        try:
            price = float(message.text.strip().replace(',', '.'))
        except ValueError:
            return await message.answer('Нужно число. Попробуй ещё:')
        if price < 100 or price > 500000:
            return await message.answer('Цена 100-500000 ₽. Попробуй ещё:')
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            if not p: 
                _user_state.pop(uid, None)
                return await message.answer('Товар не найден.')
            old = float(p.sale_price)
            p.sale_price = price
            p.price_confidence = 1.0
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ #{pid} цена {old:,.0f} → {price:,.0f} ₽', reply_markup=back_menu())

    elif state.startswith('awaiting_stock:'):
        pid = int(state.split(':')[1])
        try:
            num = int(message.text.strip())
        except ValueError:
            return await message.answer('Нужно целое число. Попробуй ещё:')
        if num < 0 or num > 10000:
            return await message.answer('Остаток 0-10000. Попробуй ещё:')
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            if not p:
                _user_state.pop(uid, None)
                return await message.answer('Товар не найден.')
            p.stock = num
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ #{pid} остаток → {num}', reply_markup=back_menu())

    elif state.startswith('awaiting_delivery:'):
        oid = int(state.split(':')[1])
        try:
            cost = float(message.text.strip().replace(',', '.'))
        except ValueError:
            return await message.answer('Нужно число. Попробуй ещё:')
        async with SessionLocal() as db:
            o = await db.get(Order, oid)
            if not o:
                _user_state.pop(uid, None)
                return await message.answer('Заказ не найден.')
            o.delivery_cost = cost
            o.total = float(o.subtotal) + cost
            o.status = 'awaiting_payment'
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ Заказ #{oid}: доставка {cost:,.0f} ₽. Итого: {float(o.total):,.0f} ₽', reply_markup=back_menu())

    elif state == 'awaiting_broadcast':
        _user_state.pop(uid, None)
        now = time.time()
        last = _broadcast_last.get(uid, 0)
        if now - last < 60:
            return await message.answer(f'Подожди {int(60 - (now - last))}с перед следующим постом.', reply_markup=back_menu())
        raw = message.text.strip()
        if len(raw) > 4000:
            return await message.answer('Текст слишком длинный (макс 4000).', reply_markup=back_menu())
        try:
            bot = Bot(settings.shop_bot_token)
            if message.reply_to_message and message.reply_to_message.photo:
                photo = message.reply_to_message.photo[-1].file_id
                await bot.send_photo(settings.shop_channel_id, photo, caption=raw, parse_mode='HTML')
            else:
                await bot.send_message(settings.shop_channel_id, raw, parse_mode='HTML')
            await bot.session.close()
            _broadcast_last[uid] = now
            await message.answer('✅ Опубликовано в канал.', reply_markup=back_menu())
        except Exception as e:
            await message.answer(f'Ошибка: {e}', reply_markup=back_menu())

# ── ЗАКАЗЫ ──

@dp.callback_query(lambda c: c.data == 'menu:orders')
async def menu_orders(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Order).order_by(Order.id.desc()).limit(20))).all()
    if not rows:
        await call.message.edit_text('Заказов нет.', reply_markup=back_menu())
        return await call.answer()
    kb_rows = []
    for o in rows:
        status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚'}.get(o.status, '📋')
        kb_rows.append([InlineKeyboardButton(text=f'{status_emoji} #{o.id} — {float(o.subtotal):,.0f} ₽', callback_data=f'order:{o.id}')])
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')])
    await call.message.edit_text(f'📋 Заказы ({len(rows)}):', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('order:'))
async def order_detail(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    oid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        o = await db.get(Order, oid)
        if not o: return await call.answer('Заказ не найден', show_alert=True)
        items = (await db.scalars(select(Product).join(OrderItem, OrderItem.product_id == Product.id).where(OrderItem.order_id == oid))).all() if False else []
    from .models import OrderItem as OI
    async with SessionLocal() as db:
        items = (await db.scalars(select(OI).where(OI.order_id == oid))).all()
    items_text = "\n".join(f"  {i.title} {f'({i.size})' if i.size else ''} x{i.quantity} = {float(i.unit_price) * i.quantity:,.0f} ₽" for i in items)
    deliv = f'{float(o.delivery_cost):,.0f} ₽' if o.delivery_cost is not None else 'уточняется'
    total = f'{float(o.total):,.0f} ₽' if o.total else '—'
    text = (f'📋 Заказ #{o.id}\n'
            f'Статус: {o.status}\n'
            f'👤 {o.customer_name} | {o.phone}\n'
            f'📍 {o.city}, {o.address}\n'
            f'💬 {o.comment or "-"}\n'
            f'TG: {o.telegram_user_id}\n\n'
            f'Товары:\n{items_text}\n\n'
            f'Товары: {float(o.subtotal):,.0f} ₽\n'
            f'Доставка: {deliv}\n'
            f'Итого: {total}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚚 Задать доставку', callback_data=f'setdelivery:{o.id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:orders')],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('setdelivery:'))
async def set_delivery_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    oid = int(call.data.split(':')[1])
    _user_state[call.from_user.id] = f'awaiting_delivery:{oid}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'order:{oid}')]
    ])
    await call.message.edit_text(f'🚚 Введи стоимость доставки для #{oid} (число в ₽):', reply_markup=kb)
    await call.answer()

# ── СТАТИСТИКА ──

@dp.callback_query(lambda c: c.data == 'menu:stats')
async def menu_stats(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        products = await db.scalar(select(func.count(Product.id))) or 0
        orders = await db.scalar(select(func.count(Order.id))) or 0
        revenue = await db.scalar(select(func.coalesce(func.sum(Order.subtotal), 0))) or 0
        pending = await db.scalar(select(func.count(Product.id)).where(Product.status == 'pending')) or 0
        published = await db.scalar(select(func.count(Product.id)).where(Product.status == 'published')) or 0
    text = (f'📊 Статистика\n\n'
            f'Товаров: {products} (ожидают: {pending}, опубликованы: {published})\n'
            f'Заказов: {orders}\n'
            f'Выручка: {float(revenue):,.0f} ₽')
    await call.message.edit_text(text, reply_markup=back_menu())
    await call.answer()

# ── РАССЫЛКА ──

@dp.callback_query(lambda c: c.data == 'menu:broadcast')
async def menu_broadcast(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    _user_state[call.from_user.id] = 'awaiting_broadcast'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='back:main')]
    ])
    await call.message.edit_text('📢 Напиши текст поста (поддерживает HTML: <b>жирный</b>, <i>курсив</i>):', reply_markup=kb)
    await call.answer()

async def main():
    await dp.start_polling(Bot(settings.admin_bot_token))

if __name__ == '__main__': asyncio.run(main())
