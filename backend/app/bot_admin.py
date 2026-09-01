import asyncio, time
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from .config import settings
from .db import SessionLocal
from .models import Product, Order, BannedProduct, SupportTicket, PromoCode, OrderItem, Review, Shipment, Referral, ReferralConfig, CartReminder, PickupPoint
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
        [InlineKeyboardButton(text='🏷 Промокоды', callback_data='menu:promos'),
         InlineKeyboardButton(text='⭐ Отзывы', callback_data='menu:reviews')],
        [InlineKeyboardButton(text='🤝 Рефералка', callback_data='menu:referrals'),
         InlineKeyboardButton(text='🎯 Сегменты', callback_data='menu:segments')],
        [InlineKeyboardButton(text='📍 Пункты выдачи', callback_data='menu:pickups'),
         InlineKeyboardButton(text='📥 Экспорт CSV', callback_data='menu:export')],
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

# ── ПРОМОКОДЫ (создание пошагово) ──

async def _handle_promo_state(uid: int, state: str, message: Message):
    if state == 'awaiting_promo_code':
        code = message.text.strip().upper()
        if len(code) < 3 or len(code) > 20:
            return await message.answer('Код 3-20 символов. Попробуй ещё:')
        async with SessionLocal() as db:
            exists = await db.scalar(select(PromoCode).where(PromoCode.code == code))
        if exists:
            return await message.answer('Такой код уже есть. Попробуй другой:')
        _user_state[uid] = f'awaiting_promo_type:{code}'
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='-percent %', callback_data=f'promotype:{code}:percent'),
             InlineKeyboardButton(text='fixed ₽', callback_data=f'promotype:{code}:fixed')],
        ])
        await message.answer(f'Код: <code>{code}</code>\nТип скидки:', parse_mode='HTML', reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith('promotype:'))
async def promo_type_selected(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    parts = call.data.split(':')
    code, dtype = parts[1], parts[2]
    _user_state[call.from_user.id] = f'awaiting_promo_value:{code}:{dtype}'
    label = '% (процент)' if dtype == 'percent' else '₽ (сумма)'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:promos')]
    ])
    await call.message.edit_text(f'Код: <code>{code}</code>\nТип: {dtype}\n\nВведи значение скидки (число {label}):', parse_mode='HTML', reply_markup=kb)
    await call.answer()

# ── ОБРАБОТКА ВВОДА (цена, остаток, доставка, промокоды, рассылка) ──

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

    elif state.startswith('awaiting_tracking_number:'):
        parts = state.split(':')
        oid, carrier = int(parts[1]), parts[2]
        tracking = message.text.strip()
        if len(tracking) < 3:
            return await message.answer('Трек-номер слишком короткий. Попробуй ещё:')
        async with SessionLocal() as db:
            shipment = Shipment(order_id=oid, carrier=carrier, tracking_number=tracking, status='registered')
            db.add(shipment)
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ Трек-номер для #{oid}: {carrier} → {tracking}', reply_markup=back_menu())

    elif state.startswith('awaiting_tracking:'):
        _user_state.pop(uid, None)
        return

    elif state.startswith('awaiting_promo_code'):
        code = message.text.strip().upper()
        if len(code) < 3 or len(code) > 20:
            return await message.answer('Код 3-20 символов. Попробуй ещё:')
        async with SessionLocal() as db:
            exists = await db.scalar(select(PromoCode).where(PromoCode.code == code))
        if exists:
            return await message.answer('Такой код уже есть. Попробуй другой:')
        _user_state[uid] = f'awaiting_promo_type:{code}'
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='% Процент', callback_data=f'promotype:{code}:percent'),
             InlineKeyboardButton(text='₽ Сумма', callback_data=f'promotype:{code}:fixed')],
        ])
        await message.answer(f'Код: <code>{code}</code>\nТип скидки:', parse_mode='HTML', reply_markup=kb)

    elif state.startswith('awaiting_promo_value:'):
        parts = state.split(':')
        code, dtype = parts[1], parts[2]
        try:
            value = float(message.text.strip().replace(',', '.'))
        except ValueError:
            return await message.answer('Нужно число. Попробуй ещё:')
        if value <= 0 or value > 100:
            return await message.answer('Значение 0-100. Попробуй ещё:')
        async with SessionLocal() as db:
            promo = PromoCode(code=code, discount_type=dtype, discount_value=value, active=True)
            db.add(promo)
            await db.commit()
        _user_state.pop(uid, None)
        disc = f'{value}%' if dtype == 'percent' else f'{value:,.0f} ₽'
        await message.answer(f'✅ Промокод <code>{code}</code> создан: {disc}', parse_mode='HTML', reply_markup=back_menu())

    elif state.startswith('awaiting_promo_'):
        _user_state.pop(uid, None)
        return

    elif state.startswith('awaiting_broadcast_segment:'):
        segment = state.split(':')[1]
        _user_state.pop(uid, None)
        raw = message.text.strip()
        if len(raw) > 4000:
            return await message.answer('Текст слишком длинный (макс 4000).', reply_markup=back_menu())
        from datetime import datetime, timedelta
        now_dt = datetime.utcnow()
        week_ago = now_dt - timedelta(weeks=1)
        async with SessionLocal() as db:
            if segment == 'buyers':
                user_ids = (await db.scalars(select(func.distinct(Order.telegram_user_id)).where(Order.created_at >= week_ago))).all()
            elif segment == 'non_buyers':
                all_users = (await db.scalars(select(func.distinct(Order.telegram_user_id)))).all()
                recent = (await db.scalars(select(func.distinct(Order.telegram_user_id)).where(Order.created_at >= week_ago))).all()
                user_ids = [u for u in all_users if u not in recent]
            else:
                user_ids = (await db.scalars(select(func.distinct(Order.telegram_user_id)))).all()
        if not user_ids:
            return await message.answer('Нет пользователей в этом сегменте.', reply_markup=back_menu())
        bot = Bot(settings.shop_bot_token)
        sent, failed = 0, 0
        for uid_seg in user_ids:
            try:
                await bot.send_message(uid_seg, raw, parse_mode='HTML')
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await bot.session.close()
        await message.answer(f'✅ Отправлено: {sent}\n❌ Ошибки: {failed}', reply_markup=back_menu())

    elif state == 'awaiting_ref_bonus':
        try:
            bonus = float(message.text.strip().replace(',', '.'))
        except ValueError:
            return await message.answer('Нужно число. Попробуй ещё:')
        if bonus < 0 or bonus > 100000:
            return await message.answer('Бонус 0-100000 ₽. Попробуй ещё:')
        async with SessionLocal() as db:
            cfg = await db.scalar(select(ReferralConfig).where(ReferralConfig.active == True))
            if cfg:
                cfg.bonus_amount = bonus
            else:
                cfg = ReferralConfig(bonus_amount=bonus, active=True)
                db.add(cfg)
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ Бонус за реферала: {bonus:,.0f} ₽', reply_markup=back_menu())

    elif state == 'awaiting_pickup_name':
        _user_state[uid] = f'awaiting_pickup_addr:{message.text.strip()}'
        await message.answer('📍 Адрес:', reply_markup=back_menu())

    elif state.startswith('awaiting_pickup_addr:'):
        name = state.split(':', 1)[1]
        _user_state[uid] = f'awaiting_pickup_hours:{name}:{message.text.strip()}'
        await message.answer('⏰ Часы работы (или —):', reply_markup=back_menu())

    elif state.startswith('awaiting_pickup_hours:'):
        parts = state.split(':', 2)
        name, addr = parts[1], parts[2]
        _user_state[uid] = f'awaiting_pickup_phone:{name}:{addr}:{message.text.strip()}'
        await message.answer('☎️ Телефон (или —):', reply_markup=back_menu())

    elif state.startswith('awaiting_pickup_phone:'):
        parts = state.split(':', 3)
        name, addr, hours = parts[1], parts[2], parts[3]
        phone = message.text.strip() if message.text.strip() != '—' else None
        async with SessionLocal() as db:
            db.add(PickupPoint(name=name, address=addr, work_hours=hours if hours != '—' else None, phone=phone))
            await db.commit()
        _user_state.pop(uid, None)
        await message.answer(f'✅ Пункт «{name}» добавлен.', reply_markup=back_menu())

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
    from .models import OrderItem as OI
    async with SessionLocal() as db:
        o = await db.get(Order, oid)
        if not o: return await call.answer('Заказ не найден', show_alert=True)
        items = (await db.scalars(select(OI).where(OI.order_id == oid))).all()
    items_text = "\n".join(f"  {i.title} {f'({i.size})' if i.size else ''} x{i.quantity} = {float(i.unit_price) * i.quantity:,.0f} ₽" for i in items)
    deliv = f'{float(o.delivery_cost):,.0f} ₽' if o.delivery_cost is not None else 'уточняется'
    total = f'{float(o.total):,.0f} ₽' if o.total else '—'
    status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚', 'assembling': '🔧', 'delivered': '🏁'}.get(o.status, '📋')
    text = (f'📋 Заказ #{o.id}\n'
            f'Статус: {status_emoji} {o.status}\n'
            f'👤 {o.customer_name} | {o.phone}\n'
            f'📍 {o.city}, {o.address}\n'
            f'💬 {o.comment or "-"}\n'
            f'TG: {o.telegram_user_id}\n\n'
            f'Товары:\n{items_text}\n\n'
            f'Товары: {float(o.subtotal):,.0f} ₽\n'
            f'Доставка: {deliv}\n'
            f'Итого: {total}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔧 Собирается', callback_data=f'status:{o.id}:assembling'),
         InlineKeyboardButton(text='🚚 Отправлен', callback_data=f'status:{o.id}:shipped')],
        [InlineKeyboardButton(text='🛵 В пути', callback_data=f'status:{o.id}:in_transit'),
         InlineKeyboardButton(text='✅ Доставлен', callback_data=f'status:{o.id}:delivered')],
        [InlineKeyboardButton(text='🚚 Доставка (цена)', callback_data=f'setdelivery:{o.id}'),
         InlineKeyboardButton(text='📦 Трек-номер', callback_data=f'settracking:{o.id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:orders')],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('status:'))
async def change_status(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    parts = call.data.split(':')
    oid, new_status = int(parts[1]), parts[2]
    status_labels = {
        'assembling': '🔧 Собирается',
        'shipped': '🚚 Отправлен',
        'in_transit': '🛵 В пути',
        'delivered': '✅ Доставлен',
    }
    async with SessionLocal() as db:
        o = await db.get(Order, oid)
        if not o: return await call.answer('Заказ не найден', show_alert=True)
        o.status = new_status
        await db.commit()
    # уведомить клиента
    try:
        bot = Bot(settings.shop_bot_token)
        await bot.send_message(
            o.telegram_user_id,
            f'📦 Заказ #{oid}\n\nСтатус изменён: <b>{status_labels.get(new_status, new_status)}</b>',
            parse_mode='HTML',
        )
        await bot.session.close()
    except Exception:
        pass
    await call.answer(f'✅ {status_labels.get(new_status, new_status)}')
    await call.message.edit_reply_markup(reply_markup=None)

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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📅 Сегодня', callback_data='stats:day'),
         InlineKeyboardButton(text='📅 Неделя', callback_data='stats:week')],
        [InlineKeyboardButton(text='📅 Месяц', callback_data='stats:month'),
         InlineKeyboardButton(text='📅 Всё время', callback_data='stats:all')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text('📊 Выбери период:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('stats:'))
async def stats_period(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    period = call.data.split(':')[1]
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if period == 'day': since = now - timedelta(days=1)
    elif period == 'week': since = now - timedelta(weeks=1)
    elif period == 'month': since = now - timedelta(days=30)
    else: since = None
    async with SessionLocal() as db:
        from .models import OrderItem as OI
        stmt = select(func.count(Order.id), func.coalesce(func.sum(Order.subtotal), 0))
        if since:
            stmt = stmt.where(Order.created_at >= since)
        result = (await db.execute(stmt)).one()
        orders_count, revenue = result[0], float(result[1])
        avg_check = revenue / orders_count if orders_count else 0
        # топ-3 товара
        top_stmt = (select(OI.title, func.sum(OI.quantity).label('qty'))
            .group_by(OI.title).order_by(func.sum(OI.quantity).desc()).limit(3))
        if since:
            from .models import Order as O2
            top_stmt = top_stmt.join(O2, O2.id == OI.order_id).where(O2.created_at >= since)
        top_rows = (await db.execute(top_stmt)).all()
    period_labels = {'day': 'Сегодня', 'week': 'Неделя', 'month': 'Месяц', 'all': 'Всё время'}
    top_text = "\n".join(f"  {i+1}. {t} — {q} шт." for i, (t, q) in enumerate(top_rows)) or "  Нет продаж"
    text = (f'📊 <b>{period_labels[period]}</b>\n\n'
            f'Заказов: {orders_count}\n'
            f'Выручка: {revenue:,.0f} ₽\n'
            f'Средний чек: {avg_check:,.0f} ₽\n\n'
            f'🏆 Топ товары:\n{top_text}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:stats')]
    ])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
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

# ── ПРОМОКОДЫ ──

@dp.callback_query(lambda c: c.data == 'menu:promos')
async def menu_promos(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        rows = (await db.scalars(select(PromoCode).order_by(PromoCode.id.desc()).limit(20))).all()
    if not rows:
        text = 'Нет промокодов.'
    else:
        lines = []
        for p in rows:
            emoji = '✅' if p.active else '❌'
            disc = f'{float(p.discount_value)}%' if p.discount_type == 'percent' else f'{float(p.discount_value):,.0f} ₽'
            lines.append(f'{emoji} <code>{p.code}</code> — {disc} | использований: {p.used_count}/{p.max_uses}')
        text = '🏷 <b>Промокоды</b>\n\n' + '\n'.join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Создать', callback_data='promo:create')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'promo:create')
async def promo_create_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    _user_state[call.from_user.id] = 'awaiting_promo_code'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:promos')]
    ])
    await call.message.edit_text('🏷 Введи код промокода (латиница, без пробелов):', reply_markup=kb)
    await call.answer()

# ── ТРЕКИНГ ДОСТАВКИ ──

@dp.callback_query(lambda c: c.data and c.data.startswith('settracking:'))
async def set_tracking_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    oid = int(call.data.split(':')[1])
    _user_state[call.from_user.id] = f'awaiting_tracking:{oid}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='CDEK', callback_data=f'tracking_carrier:{oid}:cdek'),
         InlineKeyboardButton(text='Boxberry', callback_data=f'tracking_carrier:{oid}:boxberry')],
        [InlineKeyboardButton(text='Почта', callback_data=f'tracking_carrier:{oid}:post'),
         InlineKeyboardButton(text='Другое', callback_data=f'tracking_carrier:{oid}:other')],
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'order:{oid}')],
    ])
    await call.message.edit_text(f'📦 Выбери службу доставки для #{oid}:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('tracking_carrier:'))
async def tracking_carrier_selected(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    parts = call.data.split(':')
    oid, carrier = int(parts[1]), parts[2]
    _user_state[call.from_user.id] = f'awaiting_tracking_number:{oid}:{carrier}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'order:{oid}')]
    ])
    await call.message.edit_text(f'📦 Введи трек-номер для #{oid} ({carrier}):', reply_markup=kb)
    await call.answer()

# ── ОТЗЫВЫ ──

@dp.callback_query(lambda c: c.data == 'menu:reviews')
async def menu_reviews(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Review).where(Review.status == 'pending').order_by(Review.id.desc()).limit(20))).all()
    if not rows:
        await call.message.edit_text('Нет отзывов на модерацию ✅', reply_markup=back_menu())
        return await call.answer()
    kb_rows = []
    for r in rows:
        stars = '⭐' * r.rating
        kb_rows.append([InlineKeyboardButton(text=f'{stars} #{r.id} — товар #{r.product_id}', callback_data=f'review:{r.id}')])
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')])
    await call.message.edit_text(f'⭐ Отзывы на модерацию ({len(rows)}):', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('review:') and not c.data.startswith('review_'))
async def review_detail(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    rid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        r = await db.get(Review, rid)
        if not r: return await call.answer('Отзыв не найден', show_alert=True)
        p = await db.get(Product, r.product_id)
    stars = '⭐' * r.rating
    text = (f'⭐ Отзыв #{r.id}\n'
            f'Товар: #{r.product_id} {p.title if p else "?"}\n'
            f'Оценка: {stars}\n'
            f'Текст: {r.text or "—"}\n'
            f'TG: {r.user_telegram_id}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Опубликовать', callback_data=f'review_approve:{r.id}'),
         InlineKeyboardButton(text='❌ Скрыть', callback_data=f'review_reject:{r.id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:reviews')],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('review_approve:'))
async def review_approve(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    rid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        r = await db.get(Review, rid)
        if not r: return await call.answer('Не найден', show_alert=True)
        r.status = 'approved'
        await db.commit()
    await call.answer('✅ Опубликован')
    await call.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(lambda c: c.data and c.data.startswith('review_reject:'))
async def review_reject(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    rid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        r = await db.get(Review, rid)
        if not r: return await call.answer('Не найден', show_alert=True)
        r.status = 'rejected'
        await db.commit()
    await call.answer('❌ Скрыт')
    await call.message.edit_reply_markup(reply_markup=None)

# ── РЕФЕРАЛКА ──

@dp.callback_query(lambda c: c.data == 'menu:referrals')
async def menu_referrals(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        cfg = await db.scalar(select(ReferralConfig).where(ReferralConfig.active == True))
        total_ref = await db.scalar(select(func.count(Referral.id))) or 0
        paid_ref = await db.scalar(select(func.count(Referral.id)).where(Referral.status == 'paid')) or 0
        total_bonus = await db.scalar(select(func.coalesce(func.sum(Referral.bonus_amount), 0)).where(Referral.status == 'paid')) or 0
    bonus = float(cfg.bonus_amount) if cfg else 500
    text = (f'🤝 <b>Реферальная программа</b>\n\n'
            f'Бонус за друга: {bonus:,.0f} ₽\n'
            f'Всего рефералов: {total_ref}\n'
            f'Оплачено: {paid_ref}\n'
            f'Выдано бонусов: {float(total_bonus):,.0f} ₽')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💰 Изменить бонус', callback_data='ref:set_bonus')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'ref:set_bonus')
async def ref_set_bonus_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    _user_state[call.from_user.id] = 'awaiting_ref_bonus'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:referrals')]
    ])
    await call.message.edit_text('💰 Введи сумму бонуса за реферала (₽):', reply_markup=kb)
    await call.answer()

# ── СЕГМЕНТЫ ──

@dp.callback_query(lambda c: c.data == 'menu:segments')
async def menu_segments(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    week_ago = now - timedelta(weeks=1)
    async with SessionLocal() as db:
        buyers = await db.scalar(select(func.count(func.distinct(Order.telegram_user_id))).where(Order.created_at >= week_ago)) or 0
        total_users = await db.scalar(select(func.count(func.distinct(Order.telegram_user_id)))) or 0
        non_buyers = total_users - buyers
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'🛒 Покупатели ({buyers})', callback_data='segment:buyers')],
        [InlineKeyboardButton(text=f'❌ Не покупали ({non_buyers})', callback_data='segment:non_buyers')],
        [InlineKeyboardButton(text='📢 Всем', callback_data='segment:all')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text('🎯 Выбери аудиторию для рассылки:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('segment:'))
async def segment_selected(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    segment = call.data.split(':')[1]
    _user_state[call.from_user.id] = f'awaiting_broadcast_segment:{segment}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:segments')]
    ])
    labels = {'buyers': 'покупателей', 'non_buyers': 'не покупавших', 'all': 'всех'}
    await call.message.edit_text(f'📢 Напиши текст поста для {labels.get(segment, "аудитории")} (HTML):', reply_markup=kb)
    await call.answer()

@dp.message(F.reply_to_message)
async def support_reply(message: Message):
    if not allowed(message.from_user.id): return
    if not message.reply_to_message: return
    async with SessionLocal() as db:
        ticket = await db.scalar(
            select(SupportTicket).where(
                SupportTicket.admin_chat_id == message.chat.id,
                SupportTicket.admin_message_id == message.reply_to_message.message_id,
            ).order_by(SupportTicket.id.desc())
        )
    if not ticket:
        return
    bot = Bot(settings.shop_bot_token)
    try:
        await bot.send_message(
            ticket.user_telegram_id,
            f'💬 <b>Ответ менеджера:</b>\n\n{message.text}',
            parse_mode='HTML',
        )
        await message.answer('✅ Ответ отправлен клиенту.')
    except Exception as e:
        await message.answer(f'❌ Ошибка: {e}')
    finally:
        await bot.session.close()

# ── ЛЮБОЕ ТЕКСТОВОЕ СООБЩЕНИЕ (не в стейте) ──

@dp.message(F.text)
async def on_text(message: Message):
    if not allowed(message.from_user.id): return
    uid = message.from_user.id
    state = _user_state.get(uid)
    if state:
        return
    await message.answer('NORMWEAR ADMIN', reply_markup=main_menu())

# ── ЭКСПОРТ CSV ──

@dp.callback_query(lambda c: c.data == 'menu:export')
async def menu_export(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📥 Все заказы', callback_data='export:all')],
        [InlineKeyboardButton(text='📥 За месяц', callback_data='export:month')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text('📥 Экспорт заказов в CSV:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('export:'))
async def export_csv(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    period = call.data.split(':')[1]
    await call.answer('Формирую файл...')
    from datetime import timedelta
    import io
    async with SessionLocal() as db:
        q = select(Order).order_by(Order.created_at.desc())
        if period == 'month':
            month_ago = datetime.utcnow() - timedelta(days=30)
            q = q.where(Order.created_at >= month_ago)
        orders = (await db.scalars(q)).all()
        if not orders:
            return await call.message.edit_text('Нет заказов за этот период.', reply_markup=back_menu())
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['ID', 'Телефон', 'Сумма', 'Скидка', 'Статус', 'Трек', 'Дата'])
        for o in orders:
            w.writerow([
                o.id, o.phone, float(o.total_amount), float(o.discount_amount),
                o.status, o.tracking_number or '', o.created_at.strftime('%Y-%m-%d %H:%M'),
            ])
        buf.seek(0)
        content = buf.getvalue().encode('utf-8-sig')
    from aiogram.types import BufferedInputFile
    await call.message.answer_document(
        BufferedInputFile(content, filename=f'orders_{period}.csv'),
        caption=f'📥 Заказов: {len(orders)}',
    )
    await call.message.edit_text('✅ Файл отправлен.', reply_markup=back_menu())

# ── ПУНКТЫ ВЫДАЧИ ──

@dp.callback_query(lambda c: c.data == 'menu:pickups')
async def menu_pickups(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        points = (await db.scalars(select(PickupPoint).where(PickupPoint.active == True).order_by(PickupPoint.id))).all()
    lines = []
    for p in points:
        lines.append(f'<b>{p.name}</b>\n{p.address}\n⏰ {p.work_hours or "—"} ☎️ {p.phone or "—"}')
    text = '📍 <b>Пункты выдачи</b>\n\n' + ('\n\n'.join(lines) if lines else 'Пока нет ни одного пункта.')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Добавить', callback_data='pickup:add')],
        [InlineKeyboardButton(text='🗑 Удалить', callback_data='pickup:del')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'pickup:add')
async def pickup_add_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    _user_state[call.from_user.id] = 'awaiting_pickup_name'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:pickups')]
    ])
    await call.message.edit_text('📍 Название пункта выдачи:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'pickup:del')
async def pickup_del_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        points = (await db.scalars(select(PickupPoint).where(PickupPoint.active == True))).all()
    if not points:
        return await call.message.edit_text('Нет пунктов для удаления.', reply_markup=back_menu())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'🗑 {p.name}', callback_data=f'pickup:rm:{p.id}')] for p in points
    ] + [[InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:pickups')]])
    await call.message.edit_text('Выбери пункт для удаления:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('pickup:rm:'))
async def pickup_rm(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(PickupPoint, pid)
        if p:
            await db.delete(p)
            await db.commit()
    await call.answer('Удалён')
    await call.message.edit_text('✅ Пункт удалён.', reply_markup=back_menu())

# ── ФОТО / СТИКЕРЫ ──

@dp.message(F.sticker | F.photo)
async def on_media(message: Message):
    if not allowed(message.from_user.id): return
    if message.photo and message.reply_to_message:
        return
    await message.answer('Принимаю только текст.', reply_markup=back_menu())

async def main():
    await dp.start_polling(Bot(settings.admin_bot_token))

if __name__ == '__main__': asyncio.run(main())
