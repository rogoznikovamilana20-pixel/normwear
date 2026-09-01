import asyncio
import json
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlalchemy import select, func
from .config import settings
from .db import SessionLocal
from .models import Product, Order, Favorite, PromoCode, Review, Shipment

dp = Dispatcher()

_support_mode: dict[int, bool] = {}
_review_state: dict[int, str] = {}

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
        [InlineKeyboardButton(text='❤️ Избранное', callback_data='favorites:0'),
         InlineKeyboardButton(text='💬 Поддержка', callback_data='support')]
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
    _support_mode.pop(call.from_user.id, None)
    await call.message.edit_text(
        'Добро пожаловать в <b>NORMWEAR</b>.\n\n'
        'Вся витрина и заказ — внутри Telegram.',
        parse_mode='HTML',
        reply_markup=main_menu()
    )
    await call.answer()

@dp.callback_query(F.data == 'support')
async def support_cb(call: CallbackQuery):
    _support_mode[call.from_user.id] = True
    await call.message.edit_text(
        '💬 <b>Поддержка NORMWEAR</b>\n\n'
        'Напишите ваш вопрос — менеджер ответит.\n'
        'Для выхода нажмите «⬅️ Меню».',
        parse_mode='HTML',
        reply_markup=back_menu()
    )
    await call.answer()

# ── ЛЮБОЕ ТЕКСТОВОЕ СООБЩЕНИЕ ──

@dp.message(F.text)
async def on_text(message: Message):
    text = message.text.strip()

    # отзыв — ввод текста
    state = _review_state.get(message.from_user.id)
    if state and state.startswith('review_text:'):
        parts = state.split(':')
        oid, rating = int(parts[1]), int(parts[2])
        _review_state.pop(message.from_user.id, None)
        async with SessionLocal() as db:
            order = await db.get(Order, oid)
            product_id = 0
            if order:
                items = (await db.scalars(select(OrderItem).where(OrderItem.order_id == oid))).all()
                if items:
                    product_id = items[0].product_id
            review = Review(user_telegram_id=message.from_user.id, product_id=product_id, order_id=oid, rating=rating, text=text, status='pending')
            db.add(review)
            await db.commit()
        await message.answer(f'✅ Спасибо за отзыв! {"⭐" * rating}', reply_markup=main_menu())
        return

    # поддержка — переслать админам
    if _support_mode.get(message.from_user.id):
        if text == '⬅️ Меню':
            _support_mode.pop(message.from_user.id, None)
            return await message.answer('Главное меню:', reply_markup=main_menu())
        user = message.from_user
        fwd_text = (
            f'💬 <b>Вопрос из поддержки</b>\n'
            f'От: @{user.username or "—"} (ID: {user.id})\n'
            f'Имя: {user.first_name}\n\n'
            f'{text}'
        )
        bot = Bot(settings.shop_bot_token)
        for admin_id in settings.admin_ids:
            try:
                sent = await bot.send_message(admin_id, fwd_text, parse_mode='HTML')
                # сохраняем message_id для ответа
                from .db import SessionLocal as _SL
                async with _SL() as db:
                    from .models import SupportTicket
                    ticket = SupportTicket(
                        user_telegram_id=user.id,
                        admin_chat_id=admin_id,
                        admin_message_id=sent.message_id,
                        user_message_id=message.message_id,
                    )
                    db.add(ticket)
                    await db.commit()
            except Exception:
                pass
        await bot.session.close()
        await message.answer('✅ Вопрос отправлен менеджеру. Ожидайте ответ.', reply_markup=back_menu())
        return

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
            kb_rows.append([
                InlineKeyboardButton(text=f"🛍 {p.title[:25]} — {float(p.sale_price):,.0f} ₽", web_app=WebAppInfo(url=_miniapp_url(p.id))),
            ])
            kb_rows.append([InlineKeyboardButton(text='❤️ В избранное', callback_data=f'fav:{p.id}')])
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

@dp.message(F.sticker | F.photo)
async def on_media(message: Message):
    await message.answer('Принимаю только текст. Вот меню:', reply_markup=main_menu())

# ── ИЗБРАННОЕ ──

@dp.callback_query(F.data.startswith('fav:'))
async def toggle_fav(call: CallbackQuery):
    pid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        existing = await db.scalar(select(Favorite).where(Favorite.user_telegram_id == call.from_user.id, Favorite.product_id == pid))
        if existing:
            await db.delete(existing)
            await db.commit()
            await call.answer('💔 Убрано из избранного')
        else:
            fav = Favorite(user_telegram_id=call.from_user.id, product_id=pid)
            db.add(fav)
            await db.commit()
            await call.answer('❤️ Добавлено в избранное')

@dp.callback_query(F.data.startswith('favorites:'))
async def show_favorites(call: CallbackQuery):
    try:
        page = int(call.data.split(':')[1])
    except Exception:
        page = 0
    limit = 5
    offset = page * limit
    async with SessionLocal() as db:
        fav_ids_q = select(Favorite.product_id).where(Favorite.user_telegram_id == call.from_user.id).order_by(Favorite.id.desc()).limit(limit).offset(offset)
        fav_ids = (await db.scalars(fav_ids_q)).all()
        if not fav_ids:
            await call.message.edit_text('❤️ Избранное пусто', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📦 Каталог', callback_data='catalog:0')], [InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')]]))
            return await call.answer()
        rows = (await db.scalars(select(Product).where(Product.id.in_(fav_ids)))).all()
    lines = []
    kb_rows = []
    for p in rows:
        sizes = ", ".join(json.loads(p.sizes_json)) if p.sizes_json else "—"
        lines.append(f"#{p.id} <b>{p.title}</b> — {float(p.sale_price):,.0f} ₽\nРазмеры: {sizes}")
        kb_rows.append([InlineKeyboardButton(text=f"🛍 {p.title[:30]} — {float(p.sale_price):,.0f} ₽", web_app=WebAppInfo(url=_miniapp_url(p.id)))])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'favorites:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    async with SessionLocal() as db:
        total_favs = await db.scalar(select(func.count(Favorite.id)).where(Favorite.user_telegram_id == call.from_user.id)) or 0
    if (page + 1) * limit < total_favs:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'favorites:{page+1}'))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')])
    text = f"❤️ <b>Избранное</b>\n\n" + "\n\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    try:
        await call.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode='HTML', reply_markup=kb)
    await call.answer()

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
    kb_rows = []
    for o in rows:
        status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚', 'assembling': '🔧', 'delivered': '🏁', 'in_transit': '🛵'}.get(o.status, '📋')
        delivery = f"{float(o.delivery_cost):,.0f} ₽" if o.delivery_cost is not None else "уточняется"
        total = f"{float(o.total):,.0f} ₽" if o.total is not None else "—"
        lines.append(f"{status_emoji} #{o.id} — {float(o.subtotal):,.0f} ₽ — {total}")
        kb_rows.append([InlineKeyboardButton(text=f'{status_emoji} #{o.id} — {total}', callback_data=f'myorder:{o.id}')])
    text = f"📋 <b>Ваши заказы</b>\n\n" + "\n\n".join(lines)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'myorders:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1}', callback_data='noop'))
    if nxt:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'myorders:{page+1}'))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text='⬅️ Меню', callback_data='back:main')])
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

@dp.callback_query(F.data.startswith('myorder:'))
async def myorder_detail(call: CallbackQuery):
    oid = int(call.data.split(':')[1])
    async with SessionLocal() as db:
        o = await db.get(Order, oid)
        if not o or o.telegram_user_id != call.from_user.id:
            return await call.answer('Заказ не найден', show_alert=True)
        items = (await db.scalars(select(OrderItem).where(OrderItem.order_id == oid))).all()
        shipments = (await db.scalars(select(Shipment).where(Shipment.order_id == oid))).all()
        reviews = (await db.scalars(select(Review).where(Review.order_id == oid, Review.user_telegram_id == call.from_user.id))).all()
    items_text = "\n".join(f"  {i.title} {f'({i.size})' if i.size else ''} x{i.quantity}" for i in items)
    status_emoji = {'awaiting_delivery': '📦', 'awaiting_payment': '💳', 'paid': '✅', 'shipped': '🚚', 'assembling': '🔧', 'delivered': '🏁', 'in_transit': '电动车'}.get(o.status, '📋')
    deliv = f'{float(o.delivery_cost):,.0f} ₽' if o.delivery_cost is not None else 'уточняется'
    total = f'{float(o.total):,.0f} ₽' if o.total else '—'
    tracking_text = ""
    if shipments:
        s = shipments[-1]
        tracking_text = f"\n📦 Доставка: {s.carrier}\n🔢 Трек: {s.tracking_number}"
    text = (f'{status_emoji} <b>Заказ #{o.id}</b>\n'
            f'Статус: {o.status}\n'
            f'Товары:\n{items_text}\n\n'
            f'Товары: {float(o.subtotal):,.0f} ₽\n'
            f'Доставка: {deliv}\n'
            f'Итого: {total}'
            f'{tracking_text}')
    kb_rows = []
    if o.status == 'delivered' and not reviews:
        kb_rows.append([InlineKeyboardButton(text='⭐ Оставить отзыв', callback_data=f'writereview:{o.id}')])
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='myorders:0')])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

# ── ОТЗЫВЫ ──

@dp.callback_query(F.data.startswith('writereview:'))
async def write_review_start(call: CallbackQuery):
    oid = int(call.data.split(':')[1])
    _review_state[call.from_user.id] = f'review_rating:{oid}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⭐', callback_data=f'setrating:{oid}:1'),
         InlineKeyboardButton(text='⭐⭐', callback_data=f'setrating:{oid}:2'),
         InlineKeyboardButton(text='⭐⭐⭐', callback_data=f'setrating:{oid}:3')],
        [InlineKeyboardButton(text='⭐⭐⭐⭐', callback_data=f'setrating:{oid}:4'),
         InlineKeyboardButton(text='⭐⭐⭐⭐⭐', callback_data=f'setrating:{oid}:5')],
    ])
    await call.message.edit_text('Оцени товар (нажми):', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith('setrating:'))
async def set_rating(call: CallbackQuery):
    parts = call.data.split(':')
    oid, rating = int(parts[1]), int(parts[2])
    _review_state[call.from_user.id] = f'review_text:{oid}:{rating}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Пропустить', callback_data=f'skipreview:{oid}:{rating}')]
    ])
    await call.message.edit_text(f'Оценка: {"⭐" * rating}\n\nНапиши отзыв (текст) или нажми «Пропустить»:', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith('skipreview:'))
async def skip_review_text(call: CallbackQuery):
    parts = call.data.split(':')
    oid, rating = int(parts[1]), int(parts[2])
    async with SessionLocal() as db:
        review = Review(user_telegram_id=call.from_user.id, product_id=0, order_id=oid, rating=rating, status='pending')
        db.add(review)
        await db.commit()
    _review_state.pop(call.from_user.id, None)
    await call.message.edit_text('✅ Спасибо за оценку!', reply_markup=back_menu())
    await call.answer()

@dp.callback_query(F.data == 'noop')
async def noop(call: CallbackQuery):
    await call.answer()

async def main():
    bot = Bot(settings.shop_bot_token)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
