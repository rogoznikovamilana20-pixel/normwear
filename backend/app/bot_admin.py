import asyncio, csv, time
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from .config import settings, get_shop_bot, get_admin_bot
from .db import SessionLocal
from .models import Product, Order, BannedProduct, SupportTicket, PromoCode, OrderItem, Review, Shipment, Referral, ReferralConfig, CartReminder, PickupPoint, ChatSession, ChatMessage, AdminAudit
from .publisher import ChannelPublisher
import json

_broadcast_last: dict[int, float] = {}
_user_state: dict[int, str] = {}
_chat_reply_to: dict[int, int] = {}

dp = Dispatcher()

def allowed(user_id: int) -> bool:
    return bool(settings.admin_ids) and user_id in settings.admin_ids

async def audit(admin_id: int, action: str, target: str = '', details: str = ''):
    try:
        async with SessionLocal() as db:
            db.add(AdminAudit(admin_id=admin_id, action=action, target=target, details=details))
            await db.commit()
    except Exception:
        pass

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📝 Модерация', callback_data='menu:moderation')],
        [InlineKeyboardButton(text='📸 Фото из Яндекс Диска', callback_data='menu:photos')],
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
        [InlineKeyboardButton(text='💬 Чаты', callback_data='menu:chats'),
         InlineKeyboardButton(text='📨 Шаблоны', callback_data='menu:templates')],
        [InlineKeyboardButton(text='📋 Аудит-лог', callback_data='menu:audit'),
         InlineKeyboardButton(text='⚠️ Мало остатков', callback_data='low_stock')],
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')]
    ])

@dp.message(Command('start'))
async def start(message: Message):
    if not allowed(message.from_user.id): return
    await audit(message.from_user.id, 'bot_start')
    await message.answer('NORMWEAR ADMIN', reply_markup=main_menu())

@dp.message(Command('audit'))
async def audit_cmd(message: Message):
    if not allowed(message.from_user.id): return
    await show_audit_log(message, message.from_user.id)

async def show_audit_log(target_message, viewer_id, page: int = 0):
    async with SessionLocal() as db:
        total = await db.scalar(select(func.count(AdminAudit.id))) or 0
        logs = (await db.scalars(
            select(AdminAudit).order_by(AdminAudit.id.desc()).offset(page * 8).limit(8)
        )).all()
    if not logs:
        return await target_message.answer('📋 Аудит-лог пуст.', reply_markup=back_menu())
    lines = []
    for log in logs:
        name = log.admin_name or str(log.admin_id)
        ts = log.created_at.strftime('%d.%m %H:%M')
        lines.append(f'<code>{ts}</code> <b>{name}</b> → {log.action} {log.target or ""}')
        if log.details:
            lines.append(f'  <i>{log.details[:60]}</i>')
    text = '📋 <b>Аудит-лог</b>\n\n' + '\n'.join(lines)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'audit:page:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'{page+1}/{(total-1)//8+1}', callback_data='audit:noop'))
    if (page + 1) * 8 < total:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'audit:page:{page+1}'))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    await target_message.answer(text, parse_mode='HTML', reply_markup=kb)

@dp.callback_query(lambda c: c.data == 'menu:audit')
async def menu_audit(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await show_audit_log(call.message, call.from_user.id)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('audit:page:'))
async def audit_page(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    page = int(call.data.split(':')[2])
    await show_audit_log(call.message, call.from_user.id, page)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'audit:noop')
async def audit_noop(call: CallbackQuery):
    await call.answer()

@dp.callback_query(lambda c: c.data == 'back:main')
async def back_main(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await call.message.edit_text('NORMWEAR ADMIN', reply_markup=main_menu())
    await call.answer()

# ── МОДЕРАЦИЯ ──

_MOD_STATE: dict[int, dict] = {}

def _mod_kb(pid: int, idx: int, total: int):
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'mod:prev:{pid}'))
    nav.append(InlineKeyboardButton(text=f'{idx+1}/{total}', callback_data='mod:noop'))
    if idx < total - 1:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'mod:next:{pid}'))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text='✅ Одобрить', callback_data=f'mod:approve:{pid}'),
         InlineKeyboardButton(text='✏️ Редактировать', callback_data=f'mod:edit:{pid}')],
        [InlineKeyboardButton(text='🚀 Одобрить + В канал', callback_data=f'mod:approve_publish:{pid}')],
        [InlineKeyboardButton(text='❌ Отклонить', callback_data=f'mod:reject:{pid}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])

def _edit_kb(pid: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📝 Название', callback_data=f'mod:ed_title:{pid}'),
         InlineKeyboardButton(text='📄 Описание', callback_data=f'mod:ed_desc:{pid}')],
        [InlineKeyboardButton(text='💰 Цена', callback_data=f'mod:ed_price:{pid}'),
         InlineKeyboardButton(text='📐 Размеры', callback_data=f'mod:ed_sizes:{pid}')],
        [InlineKeyboardButton(text='📂 Категория', callback_data=f'mod:ed_cat:{pid}')],
        [InlineKeyboardButton(text='✅ Сохранить', callback_data=f'mod:save:{pid}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data=f'mod:back:{pid}')],
    ])

async def _show_moderation(call: CallbackQuery, page: int = 0):
    async with SessionLocal() as db:
        products = (await db.scalars(
            select(Product).where(Product.status == 'pending', Product.channel_message_id.is_(None))
            .order_by(Product.id.desc())
        )).all()
    if not products:
        await call.message.edit_text('✅ Нет товаров на модерации', reply_markup=back_menu())
        return
    p = products[page]
    media = json.loads(p.media_json) if p.media_json else []
    sizes = json.loads(p.sizes_json) if p.sizes_json else []
    has_photos = any(m.startswith('http') for m in media)
    preview = (
        f'📝 <b>Модерация #{p.id}</b> ({page+1}/{len(products)})\n\n'
        f'<b>{p.title}</b>\n'
        f'💰 Цена: <b>{float(p.sale_price):,.0f} ₽</b>\n'
        f'📐 Размеры: {", ".join(sizes) if sizes else "—"}\n'
        f'📂 Категория: {p.category or "—"}\n'
        f'📸 Фото: {"✅" if has_photos else "❌ нет"} ({len(media)} шт)\n'
        f'📦 Остаток: {p.stock}\n'
    )
    if p.description:
        preview += f'\n📄 {p.description[:200]}'
    _MOD_STATE[call.from_user.id] = {'products': products, 'page': page}
    await call.message.edit_text(preview, parse_mode='HTML', reply_markup=_mod_kb(p.id, page, len(products)))
    await call.answer()

@dp.callback_query(lambda c: c.data == 'menu:moderation')
async def menu_moderation(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await _show_moderation(call, 0)

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:next:'))
async def mod_next(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    state = _MOD_STATE.get(call.from_user.id, {})
    products = state.get('products', [])
    page = state.get('page', 0)
    if page < len(products) - 1:
        await _show_moderation(call, page + 1)
    else:
        await call.answer('Больше нет')

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:prev:'))
async def mod_prev(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    state = _MOD_STATE.get(call.from_user.id, {})
    page = state.get('page', 0)
    if page > 0:
        await _show_moderation(call, page - 1)
    else:
        await call.answer('Больше нет')

@dp.callback_query(lambda c: c.data == 'mod:noop')
async def mod_noop(call: CallbackQuery):
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:approve:'))
async def mod_approve(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await call.answer('Товар не найден', show_alert=True)
            return
        p.status = 'approved'
        await db.commit()
    await call.answer('✅ Одобрено', show_alert=True)
    await audit(call.from_user.id, 'mod_approve', f'#{pid}', p.title[:50])
    state = _MOD_STATE.get(call.from_user.id, {})
    products = state.get('products', [])
    page = state.get('page', 0)
    if page >= len(products) - 1 and page > 0:
        page -= 1
    await _show_moderation(call, page)

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:approve_publish:'))
async def mod_approve_publish(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await call.answer('Товар не найден', show_alert=True)
            return
        p.status = 'approved'
        await db.commit()
    await call.message.edit_text('🚀 Публикация в канал...', reply_markup=back_menu())
    try:
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            media = json.loads(p.media_json) if p.media_json else []
            urls = [m for m in media if m][:6]
            if not urls:
                await call.message.edit_text('❌ Нет фото для публикации', reply_markup=back_menu())
                return
            pub = ChannelPublisher()
            msg_id = await pub.publish(p, urls)
            p.channel_message_id = msg_id
            p.status = 'published'
            await db.commit()
        await call.message.edit_text(
            f'✅ <b>Опубликовано в канал!</b>\n\n'
            f'{p.title[:50]}\n'
            f'💰 {float(p.sale_price):,.0f} ₽\n'
            f'📨 Message ID: {msg_id}',
            parse_mode='HTML', reply_markup=back_menu()
        )
        await audit(call.from_user.id, 'mod_publish', f'#{pid}', f'msg={msg_id}')
    except Exception as e:
        await call.message.edit_text(f'❌ Ошибка публикации: {e}', reply_markup=back_menu())
    state = _MOD_STATE.get(call.from_user.id, {})
    products = state.get('products', [])
    page = state.get('page', 0)
    if page >= len(products) - 1 and page > 0:
        page -= 1

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:reject:'))
async def mod_reject(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await call.answer('Товар не найден', show_alert=True)
            return
        p.status = 'rejected'
        await db.commit()
    await call.answer('❌ Отклонено', show_alert=True)
    await audit(call.from_user.id, 'mod_reject', f'#{pid}', p.title[:50])
    state = _MOD_STATE.get(call.from_user.id, {})
    products = state.get('products', [])
    page = state.get('page', 0)
    if page >= len(products) - 1 and page > 0:
        page -= 1
    await _show_moderation(call, page)

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:edit:'))
async def mod_edit(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await call.answer('Товар не найден', show_alert=True)
            return
    sizes = json.loads(p.sizes_json) if p.sizes_json else []
    text = (
        f'✏️ <b>Редактирование #{p.id}</b>\n\n'
        f'📝 <b>{p.title}</b>\n'
        f'💰 Цена: {float(p.sale_price):,.0f} ₽\n'
        f'📐 Размеры: {", ".join(sizes) if sizes else "—"}\n'
        f'📂 Категория: {p.category or "—"}\n\n'
        f'Выбери что отредактировать:'
    )
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=_edit_kb(pid))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:back:'))
async def mod_back_to_queue(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    state = _MOD_STATE.get(call.from_user.id, {})
    page = state.get('page', 0)
    await _show_moderation(call, page)

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:save:'))
async def mod_save(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    state = _MOD_STATE.get(call.from_user.id, {})
    pending = state.get('pending_edit', {})
    if pending:
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            if p:
                for field, value in pending.items():
                    setattr(p, field, value)
                await db.commit()
        _MOD_STATE[call.from_user.id]['pending_edit'] = {}
        await call.answer('✅ Сохранено', show_alert=True)
    else:
        await call.answer('Нет изменений')
    state = _MOD_STATE.get(call.from_user.id, {})
    page = state.get('page', 0)
    await _show_moderation(call, page)

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:ed_title:'))
async def mod_edit_title(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    _MOD_STATE[call.from_user.id] = {**_MOD_STATE.get(call.from_user.id, {}), 'editing': 'title', 'edit_pid': pid}
    await call.message.edit_text('📝 Введи новое название товара:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'mod:edit:{pid}')]]))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:ed_desc:'))
async def mod_edit_desc(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    _MOD_STATE[call.from_user.id] = {**_MOD_STATE.get(call.from_user.id, {}), 'editing': 'description', 'edit_pid': pid}
    await call.message.edit_text('📄 Введи новое описание товара:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'mod:edit:{pid}')]]))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:ed_price:'))
async def mod_edit_price(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    _MOD_STATE[call.from_user.id] = {**_MOD_STATE.get(call.from_user.id, {}), 'editing': 'sale_price', 'edit_pid': pid}
    await call.message.edit_text('💰 Введи новую цену (₽):', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'mod:edit:{pid}')]]))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:ed_sizes:'))
async def mod_edit_sizes(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    _MOD_STATE[call.from_user.id] = {**_MOD_STATE.get(call.from_user.id, {}), 'editing': 'sizes_json', 'edit_pid': pid}
    await call.message.edit_text('📐 Введи размеры через запятую (S, M, L, XL):', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'mod:edit:{pid}')]]))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('mod:ed_cat:'))
async def mod_edit_cat(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    _MOD_STATE[call.from_user.id] = {**_MOD_STATE.get(call.from_user.id, {}), 'editing': 'category', 'edit_pid': pid}
    await call.message.edit_text('📂 Введи новую категорию:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'mod:edit:{pid}')]]))
    await call.answer()

@dp.message(F.text & ~F.command)
async def moderation_text_input(message: Message):
    if not allowed(message.from_user.id): return
    state = _MOD_STATE.get(message.from_user.id, {})
    editing = state.get('editing')
    pid = state.get('edit_pid')
    if not editing or not pid:
        return
    text = message.text.strip()
    pending = state.get('pending_edit', {})
    if editing == 'sale_price':
        try:
            val = float(text.replace(',', '.').replace(' ', '').replace('₽', ''))
            pending['sale_price'] = val
        except ValueError:
            return await message.answer('❌ Введи число. Пример: 1500')
    elif editing == 'sizes_json':
        sizes = [s.strip().upper() for s in text.split(',') if s.strip()]
        pending['sizes_json'] = json.dumps(sizes, ensure_ascii=False)
    else:
        pending[editing] = text[:500]
    _MOD_STATE[message.from_user.id]['pending_edit'] = pending
    _MOD_STATE[message.from_user.id]['editing'] = None
    await message.answer(f'✅ Записано. Нажми "Сохранить" чтобы применить.', reply_markup=_edit_kb(pid))

# ── ФОТО ИЗ ЯНДЕКС ДИСКА ──

_PHOTO_STATE: dict[int, dict] = {}
_yandex_index: dict | None = None

def _load_yandex_index():
    global _yandex_index
    if _yandex_index is None:
        try:
            import os
            candidates = ['_yandex_index.json', '/app/_yandex_index.json']
            for path in candidates:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        _yandex_index = json.load(f)
                    break
        except Exception:
            _yandex_index = {}
    return _yandex_index

def _extract_brand(title: str) -> str | None:
    title_lower = title.lower()
    brand_map = {
        'y-3': ['y-3', 'y3', 'yohji yamamoto'],
        'nike': ['nike', 'acg'],
        'corteiz': ['corteiz', 'cor-teiz'],
        'supreme': ['supreme'],
        'undercover': ['undercover'],
        'vetements': ['vetements'],
        'bape': ['bape', 'a bathing ape'],
        'cdg': ['cdg', 'comme des garcons'],
        'chrome hearts': ['chrome heart'],
        'balenciaga': ['balenciaga'],
        'gucci': ['gucci'],
        'valentino': ['valentino'],
        'palace': ['palace'],
        'stussy': ['stussy'],
        'maison margiela': ['margiela'],
        'acne studios': ['acne'],
        'essentials': ['essentials', 'fear of essentials'],
        'off-white': ['off-white', 'off white'],
        'denim tears': ['denim tears'],
        'erd': ['erd', 'enfants riches'],
        'marcelo burlon': ['marcelo burlon'],
        'mastermind': ['mastermind'],
        'polo ralph lauren': ['polo ralph', 'ralph lauren'],
        'ami paris': ['ami paris'],
        'alyx': ['alyx'],
        'neighborhood': ['neighborhood'],
        'philip plein': ['philip plein'],
        'acme de la vie': ['acme de la vie'],
        'fred perry': ['fred perry'],
        'burberry': ['burberry'],
        'polar': ['polar'],
        'wexwear': ['wexwear'],
        'kenzo': ['kenzo'],
        'palm angels': ['palm angels'],
        'stone island': ['stone island'],
        'thom browne': ['thom browne'],
        'moncler': ['moncler'],
        'dior': ['dior'],
        'prada': ['prada'],
        'fendi': ['fendi'],
        'versace': ['versace'],
        'comme des fuckdown': ['comme des fuckdown'],
        'cdf': ['cdf'],
    }
    for brand, aliases in brand_map.items():
        for alias in aliases:
            if alias in title_lower:
                return brand
    return None

def _get_brand_photos(brand: str, category: str = None) -> list[dict]:
    idx = _load_yandex_index()
    if not idx:
        return []
    photos = []
    # Category mapping - skip categories not on disk
    NO_PHOTO_CATS = {'Шорты', 'Штаны', 'Джинсы'}
    if category and category in NO_PHOTO_CATS:
        return []
    for cat_key, cat_brands in idx.get('categories', {}).items():
        if brand in cat_brands:
            photos.extend(cat_brands[brand]['photos'])
    return photos

async def _show_photo_review(call: CallbackQuery, page: int = 0):
    async with SessionLocal() as db:
        products = (await db.scalars(
            select(Product).where(Product.media_json.is_(None) | (Product.media_json == '[]'))
            .order_by(Product.id.desc())
        )).all()
    if not products:
        await call.message.edit_text('✅ Все товары имеют фото!', reply_markup=back_menu())
        return
    p = products[page]
    brand = _extract_brand(p.title)
    photos = _get_brand_photos(brand) if brand else []
    text = (
        f'📸 <b>Фото #{p.id}</b> ({page+1}/{len(products)})\n\n'
        f'<b>{p.title[:60]}</b>\n'
        f'🏷 Бренд: {brand or "❓ неизвестен"}\n'
        f'📸 Фото на диске: {len(photos)}\n'
        f'📦 Текущие фото: нет\n'
    )
    _PHOTO_STATE[call.from_user.id] = {'products': products, 'page': page}
    kb_rows = []
    if photos:
        kb_rows.append([InlineKeyboardButton(text=f'📸 Применить все ({len(photos[:5])})', callback_data=f'photo:apply:{p.id}')])
        kb_rows.append([InlineKeyboardButton(text='⏭ Пропустить', callback_data=f'photo:skip:{p.id}')])
    else:
        kb_rows.append([InlineKeyboardButton(text='⏭ Нет фото — пропустить', callback_data=f'photo:skip:{p.id}')])
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')])
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(lambda c: c.data == 'menu:photos')
async def menu_photos(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await _show_photo_review(call, 0)

@dp.callback_query(lambda c: c.data and c.data.startswith('photo:apply:'))
async def photo_apply(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    pid = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await call.answer('Товар не найден', show_alert=True)
            return
        brand = _extract_brand(p.title)
        photos = _get_brand_photos(brand)
        if not photos:
            await call.answer('Нет фото', show_alert=True)
            return
        urls = [ph['url'] for ph in photos[:5] if ph.get('url')]
        p.media_json = json.dumps(urls, ensure_ascii=False)
        await db.commit()
    await call.answer('✅ Фото применены', show_alert=True)
    state = _PHOTO_STATE.get(call.from_user.id, {})
    page = state.get('page', 0)
    await _show_photo_review(call, page)

@dp.callback_query(lambda c: c.data and c.data.startswith('photo:skip:'))
async def photo_skip(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    state = _PHOTO_STATE.get(call.from_user.id, {})
    page = state.get('page', 0)
    products = state.get('products', [])
    if page < len(products) - 1:
        await _show_photo_review(call, page + 1)
    else:
        await call.message.edit_text('✅ Все товары обработаны!', reply_markup=back_menu())

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
        await audit(uid, 'promo_create', code, disc)

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
        now_dt = datetime.now(timezone.utc)
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
        bot = get_shop_bot()
        sent, failed = 0, 0
        for uid_seg in user_ids:
            try:
                await bot.send_message(uid_seg, raw, parse_mode='HTML')
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        await message.answer(f'✅ Отправлено: {sent}\n❌ Ошибки: {failed}', reply_markup=back_menu())
        await audit(uid, 'broadcast', segment, f'sent={sent}, failed={failed}')

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

    elif state.startswith('awaiting_chat_reply:'):
        user_id = int(state.split(':')[1])
        text = message.text.strip()
        _user_state.pop(uid, None)
        async with SessionLocal() as db:
            session = await db.scalar(select(ChatSession).where(ChatSession.user_id == user_id))
            if session:
                msg = ChatMessage(session_id=session.id, sender_id=uid, sender_role='admin', text=text)
                db.add(msg)
                session.last_message_at = datetime.now(timezone.utc)
                await db.commit()
        from aiogram import Bot
        bot = get_shop_bot()
        try:
            await bot.send_message(user_id, f'👨‍💼 <b>Ответ поддержки:</b>\n\n{text}', parse_mode='HTML')
        except Exception:
            pass

        await message.answer(f'✅ Ответ отправлен пользователю {user_id}.', reply_markup=back_menu())
        await audit(uid, 'chat_reply', str(user_id), text[:100])

    elif state == 'awaiting_template':
        text = message.text.strip()
        templates = _template_cache.get(uid, [])
        templates.append(text)
        _template_cache[uid] = templates
        _user_state.pop(uid, None)
        await message.answer(f'✅ Шаблон сохранён ({len(templates)} шт.).', reply_markup=back_menu())

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
            bot = get_shop_bot()
            if message.reply_to_message and message.reply_to_message.photo:
                photo = message.reply_to_message.photo[-1].file_id
                await bot.send_photo(settings.shop_channel_id, photo, caption=raw, parse_mode='HTML')
            else:
                await bot.send_message(settings.shop_channel_id, raw, parse_mode='HTML')
    
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
    await audit(call.from_user.id, 'order_status', f'#{oid}', status_labels.get(new_status, new_status))
    # уведомить клиента
    try:
        bot = get_shop_bot()
        status_text = status_labels.get(new_status, new_status)
        detail = {
            'assembling': 'Мы собираем ваш заказ. Скоро передадим в доставку.',
            'shipped': 'Ваш заказ передан в службу доставки. Ожидайте трек-номер.',
            'in_transit': 'Заказ в пути! Скоро будет доставлен.',
            'delivered': 'Заказ доставлен. Спасибо за покупку! 🎉',
        }.get(new_status, '')
        text = (
            f'📦 <b>Заказ #{oid}</b>\n\n'
            f'Статус: <b>{status_text}</b>\n'
            f'{detail}\n\n'
            f'Сумма: {float(o.subtotal):,.0f} ₽'
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📋 Открыть заказ', callback_data=f'myorder:{oid}')],
            [InlineKeyboardButton(text='💬 Написать в поддержку', callback_data='support')],
        ])
        await bot.send_message(o.telegram_user_id, text, parse_mode='HTML', reply_markup=kb)

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
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
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
    bot = get_shop_bot()
    try:
        await bot.send_message(
            ticket.user_telegram_id,
            f'💬 <b>Ответ менеджера:</b>\n\n{message.text}',
            parse_mode='HTML',
        )
        await message.answer('✅ Ответ отправлен клиенту.')
    except Exception as e:
        await message.answer(f'❌ Ошибка: {e}')


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
    import io
    async with SessionLocal() as db:
        q = select(Order).order_by(Order.created_at.desc())
        if period == 'month':
            month_ago = datetime.now(timezone.utc) - timedelta(days=30)
            q = q.where(Order.created_at >= month_ago)
        orders = (await db.scalars(q)).all()
        if not orders:
            return await call.message.edit_text('Нет заказов за этот период.', reply_markup=back_menu())
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['ID', 'Телефон', 'Сумма', 'Статус', 'Оплата', 'Дата'])
        for o in orders:
            w.writerow([
                o.id, o.phone, float(o.total or o.subtotal),
                o.status, o.payment_method or '', o.created_at.strftime('%Y-%m-%d %H:%M'),
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

# ── ЧАТЫ ──

@dp.callback_query(lambda c: c.data == 'menu:chats')
async def menu_chats(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        sessions = (await db.scalars(
            select(ChatSession).where(ChatSession.status == 'open').order_by(ChatSession.last_message_at.desc())
        )).all()
    if not sessions:
        return await call.message.edit_text('💬 Нет активных чатов.', reply_markup=back_menu())
    buttons = []
    for s in sessions[:10]:
        label = f'👤 {s.user_id} — {s.last_message_at.strftime("%H:%M")}'
        buttons.append([InlineKeyboardButton(text=label, callback_data=f'chat:view:{s.user_id}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')])
    await call.message.edit_text(f'💬 Активные чаты ({len(sessions)}):', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('chat:view:'))
async def chat_view(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    user_id = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        session = await db.scalar(select(ChatSession).where(ChatSession.user_id == user_id))
        if not session:
            return await call.answer('Чат не найден', show_alert=True)
        msgs = (await db.scalars(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id.desc()).limit(10)
        )).all()
    lines = []
    for m in reversed(msgs):
        prefix = '👤' if m.sender_role == 'user' else '👨‍💼'
        lines.append(f'{prefix} {m.text or "[файл]"}')
    history = '\n'.join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='↩️ Ответить', callback_data=f'chat:reply:{user_id}')],
        [InlineKeyboardButton(text='🔒 Закрыть чат', callback_data=f'chat:close:{user_id}')],
        [InlineKeyboardButton(text='⬅️ Чаты', callback_data='menu:chats')],
    ])
    await call.message.edit_text(f'💬 <b>Чат #{session.id}</b>\n👤 {user_id}\n\n{history}', parse_mode='HTML', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('chat:reply:'))
async def chat_reply_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    user_id = int(call.data.split(':')[2])
    _chat_reply_to[call.from_user.id] = user_id
    _user_state[call.from_user.id] = f'awaiting_chat_reply:{user_id}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📨 Шаблон', callback_data='templates:pick')],
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:chats')],
    ])
    await call.message.edit_text(f'↩️ Ответ пользователю {user_id}:\n\nНапиши текст:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('chat:close:'))
async def chat_close(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    user_id = int(call.data.split(':')[2])
    async with SessionLocal() as db:
        session = await db.scalar(select(ChatSession).where(ChatSession.user_id == user_id))
        if session:
            session.status = 'closed'
            await db.commit()
    await audit(call.from_user.id, 'chat_close', str(user_id))
    await call.answer('Чат закрыт')
    from aiogram import Bot
    bot = get_shop_bot()
    try:
        await bot.send_message(user_id, '🔒 Чат с поддержкой закрыт.', reply_markup=main_menu())
    except Exception:
        pass
    await call.message.edit_text(f'✅ Чат с {user_id} закрыт.', reply_markup=back_menu())

# ── ШАБЛОНЫ ──

_template_cache: dict[int, list[str]] = {}

@dp.callback_query(lambda c: c.data == 'menu:templates')
async def menu_templates(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    templates = _template_cache.get(call.from_user.id, [])
    lines = '\n'.join(f'{i+1}. {t[:40]}' for i, t in enumerate(templates)) if templates else 'Пока нет шаблонов.'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Добавить шаблон', callback_data='template:add')],
        [InlineKeyboardButton(text='🗑 Удалить шаблон', callback_data='template:del')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back:main')],
    ])
    await call.message.edit_text(f'📨 <b>Шаблоны ответов</b>\n\n{lines}', parse_mode='HTML', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'template:add')
async def template_add_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    _user_state[call.from_user.id] = 'awaiting_template'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отмена', callback_data='menu:templates')]
    ])
    await call.message.edit_text('📨 Введи текст шаблона:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == 'template:del')
async def template_del_start(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    templates = _template_cache.get(call.from_user.id, [])
    if not templates:
        return await call.message.edit_text('Нет шаблонов.', reply_markup=back_menu())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'🗑 {t[:30]}', callback_data=f'template:rm:{i}')] for i, t in enumerate(templates)
    ] + [[InlineKeyboardButton(text='⬅️ Назад', callback_data='menu:templates')]])
    await call.message.edit_text('Выбери шаблон для удаления:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('template:rm:'))
async def template_rm(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    idx = int(call.data.split(':')[2])
    templates = _template_cache.get(call.from_user.id, [])
    if 0 <= idx < len(templates):
        templates.pop(idx)
    await call.answer('Удалён')
    await call.message.edit_text('✅ Шаблон удалён.', reply_markup=back_menu())

@dp.callback_query(lambda c: c.data == 'templates:pick')
async def template_pick(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    templates = _template_cache.get(call.from_user.id, [])
    if not templates:
        return await call.answer('Нет шаблонов', show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t[:40], callback_data=f'tpl:use:{i}')] for i, t in enumerate(templates)
    ] + [[InlineKeyboardButton(text='❌ Назад', callback_data='menu:chats')]])
    await call.message.edit_text('📨 Выбери шаблон:', reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith('tpl:use:'))
async def template_use(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    idx = int(call.data.split(':')[2])
    templates = _template_cache.get(call.from_user.id, [])
    if 0 <= idx < len(templates):
        text = templates[idx]
        user_id = _chat_reply_to.get(call.from_user.id)
        if user_id:
            async with SessionLocal() as db:
                session = await db.scalar(select(ChatSession).where(ChatSession.user_id == user_id))
                if session:
                    msg = ChatMessage(session_id=session.id, sender_id=call.from_user.id, sender_role='admin', text=text)
                    db.add(msg)
                    session.last_message_at = datetime.now(timezone.utc)
                    await db.commit()
            from aiogram import Bot
            bot = get_shop_bot()
            try:
                await bot.send_message(user_id, f'👨‍💼 <b>Ответ поддержки:</b>\n\n{text}', parse_mode='HTML')
            except Exception:
                pass
    
            _user_state.pop(call.from_user.id, None)
            _chat_reply_to.pop(call.from_user.id, None)
            await call.message.edit_text(f'✅ Шаблон отправлен.', reply_markup=back_menu())
        else:
            await call.answer('Нет активного ответа', show_alert=True)
    await call.answer()

# ── АНАЛИТИКА ──

@dp.callback_query(F.data == 'analytics')
async def show_analytics(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        from .models import OrderItem, Review
        total_orders = await db.scalar(select(func.count(Order.id))) or 0
        total_revenue = await db.scalar(select(func.coalesce(func.sum(Order.total), 0))) or 0
        paid_orders = await db.scalar(select(func.count(Order.id)).where(Order.status == 'paid')) or 0
        published = await db.scalar(select(func.count(Product.id)).where(Product.status == 'published')) or 0
        total_users = await db.scalar(select(func.count(func.distinct(Order.telegram_user_id)))) or 0
        total_reviews = await db.scalar(select(func.count(Review.id)).where(Review.status == 'approved')) or 0
        avg_rating = await db.scalar(select(func.coalesce(func.avg(Review.rating), 0)).where(Review.status == 'approved')) or 0
        top = (await db.execute(
            select(OrderItem.title, func.count(OrderItem.id).label('cnt'))
            .group_by(OrderItem.title).order_by(func.count(OrderItem.id).desc()).limit(5)
        )).all()
        status_counts = (await db.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )).all()
    status_emoji = {'awaiting_delivery':'📦','awaiting_payment':'💳','paid':'✅','shipped':'🚚','assembling':'🔧','delivered':'🏁','in_transit':'🛵'}
    status_text = '\n'.join(f'  {status_emoji.get(s, "📋")} {s}: {c}' for s, c in status_counts)
    top_text = '\n'.join(f'  {i+1}. {t[:30]} — {c} шт.' for i, (t, c) in enumerate(top))
    text = (
        f'📊 <b>Аналитика магазина</b>\n\n'
        f'📦 Заказов: {total_orders} (оплачено: {paid_orders})\n'
        f'💰 Выручка: {float(total_revenue):,.0f} ₽\n'
        f'👤 Покупателей: {total_users}\n'
        f'📦 Товаров в каталоге: {published}\n'
        f'⭐ Отзывов: {total_reviews} (ср. {float(avg_rating):.1f})\n\n'
        f'📈 <b>Статусы заказов:</b>\n{status_text}\n\n'
        f'🏆 <b>Топ товаров:</b>\n{top_text}'
    )
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=back_menu())
    await call.answer()

# ── МАЛО ОСТАТКОВ ──

@dp.callback_query(F.data == 'low_stock')
async def show_low_stock(call: CallbackQuery):
    if not allowed(call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    async with SessionLocal() as db:
        low = (await db.scalars(
            select(Product).where(Product.status == 'published', Product.stock <= 2, Product.stock > 0).order_by(Product.stock)
        )).all()
    if not low:
        await call.message.edit_text('✅ Все товары в наличии.', reply_markup=back_menu())
        return await call.answer()
    lines = [f'⚠️ #{p.id} <b>{p.title[:40]}</b> — остаток: {p.stock}' for p in low[:20]]
    text = f'⚠️ <b>Мало остатков ({len(low)} товаров):</b>\n\n' + '\n'.join(lines)
    await call.message.edit_text(text, parse_mode='HTML', reply_markup=back_menu())
    await call.answer()

# ── PUBLISH COMMAND ──

@dp.message(Command('publish'))
async def cmd_publish(message: Message):
    if not allowed(message.from_user.id): return
    args = (message.text or '').split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer('Использование: /publish ID\n\nПример: /publish 6169', reply_markup=back_menu())
        return
    pid = int(args[1].strip())
    await message.answer(f'⏳ Публикация товара #{pid}...', reply_markup=back_menu())
    try:
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            if not p:
                await message.answer(f'❌ Товар #{pid} не найден', reply_markup=back_menu())
                return
            if float(p.purchase_price) > 0 and float(p.sale_price) <= float(p.purchase_price):
                p.sale_price = round(float(p.purchase_price) * (1 + settings.default_margin_pct / 100))
                await db.commit()
                await db.refresh(p)
        from .publisher import ChannelPublisher
        pub = ChannelPublisher()
        media = json.loads(p.media_json)
        http_urls = [m for m in media if m.startswith('http')]
        if not http_urls:
            await message.answer(f'❌ У товара #{pid} нет фото', reply_markup=back_menu())
            return
        msg_id = await pub.publish(p, http_urls[:6])
        async with SessionLocal() as db:
            p = await db.get(Product, pid)
            p.channel_message_id = msg_id
            await db.commit()
        await message.answer(f'✅ Опубликовано!\n\nТовар: {p.title[:50]}\nЦена: {float(p.sale_price):,.0f} ₽\nMsg ID: {msg_id}', reply_markup=back_menu())
    except Exception as e:
        await message.answer(f'❌ Ошибка: {e}', reply_markup=back_menu())

@dp.message(Command('bulkmark'))
async def cmd_bulkmark(message: Message):
    if not allowed(message.from_user.id): return
    args = (message.text or '').split()
    pct = settings.default_margin_pct
    if len(args) >= 2 and args[1].replace('.', '').isdigit():
        pct = float(args[1])
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Product).where(Product.status == 'published'))).all()
        updated = 0
        for p in rows:
            if float(p.purchase_price) > 0 and float(p.sale_price) <= float(p.purchase_price):
                p.sale_price = round(float(p.purchase_price) * (1 + pct / 100))
                updated += 1
        await db.commit()
    await message.answer(f'✅ Наценка {pct}% применена\nОбновлено: {updated} товаров', reply_markup=back_menu())

@dp.message(Command('sync'))
async def cmd_sync(message: Message):
    if not allowed(message.from_user.id): return
    args = (message.text or '').split()
    days = 7
    if len(args) >= 2 and args[1].isdigit():
        days = int(args[1])
    await message.answer(f'⏳ Синхронизация с поставщиком ({days} дней)...', reply_markup=back_menu())
    try:
        from .pipeline import ingest_supplier
        result = await ingest_supplier(days=days)
        text = (f'✅ Синхронизация завершена\n\n'
                f'📝 Постов обработано: {result["total_posts"]}\n'
                f'🆕 Создано товаров: {result["created"]}\n'
                f'⏭️ Пропущено: {result["skipped"]}\n'
                f'📤 Опубликовано: {result["published"]}\n'
                f'❌ Ошибок: {result["errors"]}')
        await message.answer(text, reply_markup=back_menu())
    except Exception as e:
        await message.answer(f'❌ Ошибка: {e}', reply_markup=back_menu())

@dp.message(Command('ingestion'))
async def cmd_ingestion(message: Message):
    if not allowed(message.from_user.id): return
    from .models import SourcePost, ProcessingJob
    async with SessionLocal() as db:
        total = await db.scalar(select(func.count(SourcePost.id))) or 0
        processed = await db.scalar(select(func.count(SourcePost.id)).where(SourcePost.processing_status == 'processed')) or 0
        skipped = await db.scalar(select(func.count(SourcePost.id)).where(SourcePost.processing_status.like('skipped%'))) or 0
        errors = await db.scalar(select(func.count(SourcePost.id)).where(SourcePost.processing_status.like('error%'))) or 0
        products = await db.scalar(select(func.count(Product.id))) or 0
        published = await db.scalar(select(func.count(Product.id)).where(Product.status == 'published')) or 0
        jobs_retry = await db.scalar(select(func.count(ProcessingJob.id)).where(ProcessingJob.status == 'retry')) or 0
        jobs_failed = await db.scalar(select(func.count(ProcessingJob.id)).where(ProcessingJob.status == 'failed')) or 0
    text = (f'📊 <b>Статистика ingestion</b>\n\n'
            f'📥 Всего постов: {total}\n'
            f'✅ Обработано: {processed}\n'
            f'⏭️ Пропущено: {skipped}\n'
            f'❌ Ошибок: {errors}\n\n'
            f'🛍 Товаров: {products}\n'
            f'📤 Опубликовано: {published}\n\n'
            f'🔄 Retry: {jobs_retry}\n'
            f'⛔ Failed: {jobs_failed}')
    await message.answer(text, parse_mode='HTML', reply_markup=back_menu())

# ── ФОТО / СТИКЕРЫ ──

@dp.message(F.sticker | F.photo)
async def on_media(message: Message):
    if not allowed(message.from_user.id): return
    if message.photo and message.reply_to_message:
        return
    await message.answer('Принимаю только текст.', reply_markup=back_menu())

async def main():
    await dp.start_polling(get_admin_bot())

if __name__ == '__main__': asyncio.run(main())
