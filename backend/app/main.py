import json
import hashlib
import hmac
import time as _time
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from sqlalchemy import select
from .db import SessionLocal
from .models import Product, Order, OrderItem
from .auth import validate_init_data
from .config import settings, get_shop_bot, get_admin_bot

def _require_admin(request: Request) -> None:
    if not settings.admin_ids:
        return
    token = request.headers.get('x-admin-token', '')
    if not token:
        raise HTTPException(401, 'Admin token required')
    import hashlib, hmac as _hmac
    expected = hashlib.sha256(f"normwear-{settings.admin_bot_token[-8:]}".encode()).hexdigest()[:32]
    if not _hmac.compare_digest(token, expected):
        raise HTTPException(403, 'Invalid admin token')

def _rate_key(request: Request) -> str:
    # prefer telegram user id from header if present, else IP
    init = request.headers.get('x-telegram-init-data', '')
    if init:
        try:
            data = validate_init_data(init)
            uid = (data.get('user') or {}).get('id')
            if uid:
                return f"tg:{uid}"
        except Exception:
            pass
    return get_remote_address(request)

limiter = Limiter(key_func=_rate_key, default_limits=[])

import os as _os, asyncio
from contextlib import asynccontextmanager

_bot_status = {"shop": "not_started", "admin": "not_started"}

@asynccontextmanager
async def lifespan(app_instance):
    from .bot_shop import dp as shop_dp
    from .bot_admin import dp as admin_dp
    from aiogram import Bot
    print("[lifespan] imports OK", flush=True)

    webhook_url = getattr(settings, 'webhook_url', '') or ''

    async def _run_bot(name: str, token: str, dp):
        _bot_status[name] = "starting"
        if webhook_url:
            # WEBHOOK MODE
            try:
                bot = Bot(token)
                await bot.delete_webhook(drop_pending_updates=True)
                wh = f"{webhook_url.rstrip('/')}/webhook/{name}"
                await bot.set_webhook(wh, drop_pending_updates=True)
                await bot.session.close()
                _bot_status[name] = "webhook"
                print(f"[{name}] webhook set: {wh}", flush=True)
            except Exception as e:
                print(f"[{name}] webhook error: {e}", flush=True)
                _bot_status[name] = f"webhook_error: {e}"
        else:
            # POLLING MODE
            backoff = 1
            while True:
                try:
                    bot = Bot(token)
                    await bot.delete_webhook(drop_pending_updates=True)
                    _bot_status[name] = "polling"
                    print(f"[{name}] starting polling", flush=True)
                    await dp.start_polling(bot)
                    _bot_status[name] = "stopped"
                    print(f"[{name}] polling ended normally", flush=True)
                except Exception as e:
                    import traceback; traceback.print_exc()
                    _bot_status[name] = f"crashed: {e}"
                    print(f"[{name}] polling crashed: {e}, restarting in {backoff}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _run_supplier():
        try:
            from .supplier_daemon import main as sup_main
            print("[supplier] starting", flush=True)
            await sup_main()
        except Exception as e:
            print(f"[supplier] error {e}", flush=True)

    if settings.shop_bot_token and len(settings.shop_bot_token) > 20:
        asyncio.create_task(_run_bot("shop", settings.shop_bot_token, shop_dp))
    if settings.admin_bot_token and len(settings.admin_bot_token) > 20:
        asyncio.create_task(_run_bot("admin", settings.admin_bot_token, admin_dp))
    asyncio.create_task(_run_supplier())
    print("[lifespan] bots+supplier scheduled", flush=True)
    yield

app = FastAPI(title='NORMWEAR Commerce API', version='1.0.0', lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda r, e: JSONResponse(status_code=429, content={"detail": "Слишком много запросов, попробуйте позже"}))

# CORS for Telegram Mini App + local dev
allowed_origins = ["https://telegram.org", "https://web.telegram.org", "https://t.me"]
# add domain from MINIAPP_URL_TEMPLATE if set
try:
    from urllib.parse import urlparse
    _u = urlparse(settings.miniapp_url_template)
    if _u.scheme and _u.netloc:
        allowed_origins.append(f"{_u.scheme}://{_u.netloc}")
except Exception:
    pass
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://.*\.telegram\.org|https://.*\.t\.me|http://localhost.*|http://127\.0\.0\.1.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for _p in ('/app/miniapp/dist', '/app/miniapp', 'miniapp/dist', 'miniapp', '../miniapp/dist', '../miniapp'):
    if _os.path.isdir(_p):
        app.mount('/app', StaticFiles(directory=_p, html=True), name='miniapp')
        break

class CartLine(BaseModel):
    product_id: int
    quantity: int = 1
    size: str | None = None

class Checkout(BaseModel):
    lines: list[CartLine]
    name: str
    phone: str
    city: str
    address: str
    comment: str | None = None
    payment_method: str = 'sbp'
    promo_code: str | None = None

    def validate_fields(self):
        for field in ('name', 'phone', 'city', 'address'):
            val = getattr(self, field, '')
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f'Поле {field} обязательно')
            if len(val.strip()) < 2:
                raise ValueError(f'Поле {field} слишком короткое')
        if len(self.phone.strip()) < 7:
            raise ValueError('Укажите корректный телефон')

@app.get('/health')
async def health(): return {'status': 'ok'}

@app.get('/bot-status')
async def bot_status(): return _bot_status

# ── WEBHOOK SUPPORT (optional, use WEBHOOK_URL env var) ──

from aiogram.types import Update as AiogramUpdate

@app.post('/webhook/shop')
async def webhook_shop(request: Request):
    body = await request.json()
    update = AiogramUpdate.model_validate(body)
    await shop_dp.feed_update(get_shop_bot(), update)
    return {"ok": True}

@app.post('/webhook/admin')
async def webhook_admin(request: Request):
    body = await request.json()
    update = AiogramUpdate.model_validate(body)
    await admin_dp.feed_update(get_admin_bot(), update)
    return {"ok": True}

@app.get('/metrics')
async def metrics():
    # simple prometheus-style metrics without extra deps
    from sqlalchemy import func, select as sel
    async with SessionLocal() as db:
        p_cnt = await db.scalar(sel(func.count(Product.id))) or 0
        o_cnt = await db.scalar(sel(func.count(Order.id))) or 0
        pend = await db.scalar(sel(func.count(Product.id)).where(Product.status=='pending')) or 0
        pub = await db.scalar(sel(func.count(Product.id)).where(Product.status=='published')) or 0
        await_cnt = await db.scalar(sel(func.count(Order.id)).where(Order.status=='awaiting_delivery')) or 0
    return {
        "products_total": p_cnt,
        "products_pending": pend,
        "products_published": pub,
        "orders_total": o_cnt,
        "orders_awaiting_delivery": await_cnt,
    }

@app.get('/api/products')
async def products(limit: int = 20, offset: int = 0, category: str | None = None, q: str | None = None):
    if limit < 1: limit = 1
    if limit > 100: limit = 100
    if offset < 0: offset = 0
    async with SessionLocal() as db:
        stmt = select(Product).where(Product.status == 'published', Product.stock > 0)
        if category and category != 'Все':
            stmt = stmt.where(Product.category == category)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(Product.title.ilike(like))
        stmt = stmt.order_by(Product.created_at.desc()).limit(limit).offset(offset)
        rows = (await db.scalars(stmt)).all()
        # total for pagination header
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(Product).where(Product.status == 'published', Product.stock > 0)
        if category and category != 'Все':
            count_stmt = count_stmt.where(Product.category == category)
        if q and q.strip():
            count_stmt = count_stmt.where(Product.title.ilike(like))
        total = await db.scalar(count_stmt) or 0
    def _media_urls(p):
        raw = json.loads(p.media_json)
        urls = []
        for i, item in enumerate(raw):
            if item.startswith('http'):
                urls.append(item)
            else:
                urls.append(f'/media/{p.id}/{i}')
        return urls

    data = [{
        'id': p.id, 'title': p.title, 'description': p.description or '', 'category': p.category,
        'price': float(p.sale_price), 'stock': p.stock, 'sizes': json.loads(p.sizes_json),
        'media': _media_urls(p)
    } for p in rows]
    return JSONResponse(content=data, headers={"X-Total-Count": str(total), "X-Limit": str(limit), "X-Offset": str(offset)})

@app.get('/api/products/{product_id}')
async def product(product_id: int):
    async with SessionLocal() as db: p = await db.get(Product, product_id)
    if not p or p.status not in {'published','approved'}: raise HTTPException(404, 'Product not found')
    raw = json.loads(p.media_json)
    media = [item if item.startswith('http') else f'/media/{p.id}/{i}' for i, item in enumerate(raw)]
    return {'id':p.id,'title':p.title,'description':p.description or '','category':p.category,'price':float(p.sale_price),'stock':p.stock,'sizes':json.loads(p.sizes_json),'media':media}

@app.get('/media/{product_id}/{index}')
async def media(product_id: int, index: int):
    async with SessionLocal() as db: p = await db.get(Product, product_id)
    if not p: raise HTTPException(404, 'Product not found')
    items = json.loads(p.media_json)
    if index < 0 or index >= len(items): raise HTTPException(404, 'Media not found')
    raw = items[index]
    # sanitize: only allow paths inside media/supplier or /app/media/supplier
    import pathlib
    allowed_roots = [pathlib.Path('/app/media/supplier').resolve(), pathlib.Path('media/supplier').resolve(), pathlib.Path('backend/media/supplier').resolve()]
    try:
        target = pathlib.Path(raw).resolve()
    except Exception:
        raise HTTPException(404, 'Media not found')
    if not any(str(target).startswith(str(r)) for r in allowed_roots) and not target.is_file():
        # fallback: also allow exact stored path if file exists, otherwise 404
        if not pathlib.Path(raw).is_file():
            raise HTTPException(404, 'Media not found')
        return FileResponse(raw)
    if not target.is_file():
        raise HTTPException(404, 'Media not found')
    return FileResponse(str(target))

@app.post('/api/products')
@limiter.limit("60/minute")
async def create_product(request: Request):
    _require_admin(request)
    from .models import Product
    try:
        body = await request.json()
        title = body.get('title', '').strip()[:200]
        if not title:
            raise HTTPException(400, 'title required')
        async with SessionLocal() as db:
            existing = await db.scalar(select(Product).where(Product.title == title))
            if existing:
                raise HTTPException(409, 'exists')
            p = Product(
                supplier_chat='@optobaza',
                supplier_message_id=0,
                title=title,
                description=body.get('description', ''),
                category=body.get('category', ''),
                purchase_price=float(body.get('purchase_price', 0)),
                sale_price=float(body.get('sale_price', 0)),
                sizes_json=json.dumps(body.get('sizes_json', [])),
                stock=body.get('stock', 1),
                status='draft',
            )
            db.add(p)
            await db.commit()
            await db.refresh(p)
            if settings.auto_publish and p.status == 'draft':
                try:
                    from .publisher import publish_product
                    await publish_product(p.id)
                except Exception as e:
                    print(f'Auto-publish error: {e}', flush=True)
            return {'id': p.id, 'title': p.title}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={'detail': str(e)})

@app.post('/api/products/bulk')
async def bulk_create(request: Request):
    _require_admin(request)
    from .models import Product
    try:
        body = await request.json()
        items = body.get('products', [])
        if not items or len(items) > 100:
            raise HTTPException(400, 'products array required, max 100')
        created = 0
        skipped = 0
        async with SessionLocal() as db:
            for item in items:
                title = str(item.get('title', '')).strip()[:200]
                if not title:
                    skipped += 1
                    continue
                existing = await db.scalar(select(Product).where(Product.title == title))
                if existing:
                    skipped += 1
                    continue
                p = Product(
                    supplier_chat='@optobaza',
                    supplier_message_id=0,
                    title=title,
                    description=str(item.get('description', ''))[:500],
                    category=str(item.get('category', '')),
                    purchase_price=float(item.get('purchase_price', 0)),
                    sale_price=float(item.get('sale_price', 0)),
                    sizes_json=json.dumps(item.get('sizes_json', [])),
                    stock=min(int(item.get('stock', 1)), 100),
                    status='draft',
                )
                db.add(p)
                created += 1
            await db.commit()
        return {'created': created, 'skipped': skipped}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={'detail': str(e)})

@app.post('/api/products/bulk-publish')
@limiter.limit("5/minute")
async def bulk_publish(request: Request):
    _require_admin(request)
    from .models import Product
    async with SessionLocal() as db:
        result = await db.execute(
            select(Product).where(Product.status == 'draft')
        )
        products = result.scalars().all()
        count = 0
        for p in products:
            p.status = 'published'
            count += 1
        await db.commit()
    return {'published': count}

@app.post('/api/products/bulk-update')
async def bulk_update(request: Request):
    _require_admin(request)
    from .models import Product
    import re as _re
    try:
        body = await request.json()
        updates = body.get('updates', [])
        if not updates or len(updates) > 200:
            raise HTTPException(400, 'updates array required, max 200')
        updated = 0
        not_found = 0
        async with SessionLocal() as db:
            for u in updates:
                title = str(u.get('title', '')).strip()[:200]
                if not title:
                    not_found += 1
                    continue
                p = await db.scalar(select(Product).where(Product.title == title))
                if not p:
                    not_found += 1
                    continue
                if u.get('media_json') is not None:
                    p.media_json = json.dumps(u['media_json'])
                if u.get('sizes_json') is not None:
                    p.sizes_json = json.dumps(u['sizes_json'])
                if u.get('description') is not None:
                    p.description = str(u['description'])[:500]
                if u.get('category') is not None:
                    p.category = str(u['category'])[:128]
                if u.get('purchase_price') is not None:
                    p.purchase_price = float(u['purchase_price'])
                if u.get('sale_price') is not None:
                    p.sale_price = float(u['sale_price'])
                if u.get('brand') is not None:
                    p.brand = str(u['brand'])[:128]
                updated += 1
            await db.commit()
        return {'updated': updated, 'not_found': not_found}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={'detail': str(e)})

# ── CATALOG CACHE (60s TTL) ──

_catalog_cache: dict = {"data": None, "ts": 0}
CATALOG_CACHE_TTL = 60

@app.get('/api/catalog')
async def catalog_cached():
    now = _time.time()
    if _catalog_cache["data"] and now - _catalog_cache["ts"] < CATALOG_CACHE_TTL:
        return JSONResponse(content=_catalog_cache["data"])
    async with SessionLocal() as db:
        rows = (await db.scalars(
            select(Product).where(Product.status == 'published', Product.stock > 0)
            .order_by(Product.created_at.desc()).limit(100)
        )).all()
    def _media_urls(p):
        raw = json.loads(p.media_json)
        return [item if item.startswith('http') else f'/media/{p.id}/{i}' for i, item in enumerate(raw)]
    data = [{'id': p.id, 'title': p.title, 'description': p.description or '', 'category': p.category,
             'price': float(p.sale_price), 'stock': p.stock, 'sizes': json.loads(p.sizes_json),
             'media': _media_urls(p)} for p in rows]
    _catalog_cache["data"] = data
    _catalog_cache["ts"] = now
    return JSONResponse(content=data)

# ── REVIEWS API ──

@app.get('/api/reviews/{product_id}')
async def product_reviews(product_id: int):
    from .models import Review
    async with SessionLocal() as db:
        reviews = (await db.scalars(
            select(Review).where(Review.product_id == product_id, Review.status == 'approved')
            .order_by(Review.id.desc()).limit(20)
        )).all()
    return [{'id': r.id, 'rating': r.rating, 'text': r.text or '', 'user': r.user_telegram_id} for r in reviews]

@app.get('/api/reviews-stats/{product_id}')
async def review_stats(product_id: int):
    from .models import Review
    from sqlalchemy import func
    async with SessionLocal() as db:
        result = (await db.execute(
            select(func.count(Review.id), func.coalesce(func.avg(Review.rating), 0))
            .where(Review.product_id == product_id, Review.status == 'approved')
        )).one()
    count, avg_rating = result[0], float(result[1])
    return {'count': count, 'avg_rating': round(avg_rating, 1)}

@app.get('/api/favorites')
async def get_favorites(request: Request, x_telegram_init_data: str = Header(default='')):
    try:
        init = validate_init_data(x_telegram_init_data)
        user_id = int((init.get('user') or {}).get('id', 0))
    except: raise HTTPException(401, 'Auth required')
    from .models import Favorite
    async with SessionLocal() as db:
        favs = (await db.scalars(select(Favorite).where(Favorite.user_telegram_id == user_id))).all()
    return [{'id': f.product_id} for f in favs]

@app.post('/api/favorites/{product_id}')
async def toggle_favorite(product_id: int, request: Request, x_telegram_init_data: str = Header(default='')):
    try:
        init = validate_init_data(x_telegram_init_data)
        user_id = int((init.get('user') or {}).get('id', 0))
    except: raise HTTPException(401, 'Auth required')
    from .models import Favorite
    async with SessionLocal() as db:
        existing = await db.scalar(select(Favorite).where(Favorite.user_telegram_id == user_id, Favorite.product_id == product_id))
        if existing:
            await db.delete(existing)
            await db.commit()
            return {'action': 'removed'}
        else:
            db.add(Favorite(user_telegram_id=user_id, product_id=product_id))
            await db.commit()
            return {'action': 'added'}

@app.get('/api/my-orders')
async def my_orders(request: Request, x_telegram_init_data: str = Header(default='')):
    try:
        init = validate_init_data(x_telegram_init_data)
        user_id = int((init.get('user') or {}).get('id', 0))
    except: raise HTTPException(401, 'Auth required')
    async with SessionLocal() as db:
        orders = (await db.scalars(
            select(Order).where(Order.telegram_user_id == user_id).order_by(Order.id.desc()).limit(20)
        )).all()
    return [{'id': o.id, 'status': o.status, 'total': float(o.total) if o.total else None, 'created_at': o.created_at.strftime('%d.%m.%Y %H:%M') if o.created_at else ''} for o in orders]

@app.delete('/api/products/{product_id}')
async def delete_product(product_id: int, request: Request):
    _require_admin(request)
    async with SessionLocal() as db:
        p = await db.get(Product, product_id)
        if not p: raise HTTPException(404, 'Product not found')
        await db.delete(p)
        await db.commit()
    return {'deleted': product_id}

@app.post('/api/session/validate')
@limiter.limit("30/minute")
async def validate(request: Request, x_telegram_init_data: str = Header(default='')):
    try: return validate_init_data(x_telegram_init_data)
    except ValueError as e: raise HTTPException(401, str(e))

class PromoRequest(BaseModel):
    code: str

@app.post('/api/promo/validate')
@limiter.limit("10/minute")
async def promo_validate(request: Request, promo: PromoRequest, x_telegram_init_data: str = Header(default='')):
    try: validate_init_data(x_telegram_init_data)
    except: raise HTTPException(401, 'Auth required')
    from .models import PromoCode
    from datetime import datetime
    async with SessionLocal() as db:
        p = await db.scalar(select(PromoCode).where(PromoCode.code == promo.code.upper(), PromoCode.active == True))
        if not p: raise HTTPException(404, 'Промокод не найден')
        if p.expires_at and p.expires_at < datetime.now(timezone.utc): raise HTTPException(400, 'Промокод истёк')
        if p.used_count >= p.max_uses: raise HTTPException(400, 'Промокод использован')
    return {'code': p.code, 'discount_type': p.discount_type, 'discount_value': float(p.discount_value), 'min_order': float(p.min_order)}

@app.post('/api/orders')
@limiter.limit("5/minute")
async def create_order(request: Request, checkout: Checkout, x_telegram_init_data: str = Header(default='')):
    try:
        init = validate_init_data(x_telegram_init_data)
        user = init.get('user') or {}
        telegram_user_id = int(user['id'])
    except Exception as e:
        raise HTTPException(401, f'Telegram authorization required: {e}')
    try:
        checkout.validate_fields()
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not checkout.lines: raise HTTPException(400, 'Cart is empty')
    # deduplicate lines by (product_id, size) to avoid double counting
    merged: dict[tuple[int, str | None], int] = {}
    for line in checkout.lines:
        if line.quantity < 1 or line.quantity > 20: raise HTTPException(400, 'Invalid quantity')
        key = (line.product_id, line.size)
        merged[key] = merged.get(key, 0) + line.quantity
        if merged[key] > 20: raise HTTPException(400, 'Invalid quantity')
    async with SessionLocal() as db:
        async with db.begin():
            verified = []
            subtotal = Decimal('0')
            for (pid, size), qty in merged.items():
                # row-level lock to prevent oversell
                p = await db.scalar(select(Product).where(Product.id == pid).with_for_update())
                if not p or p.status != 'published' or p.stock < qty: raise HTTPException(409, f'Product unavailable: {pid}')
                if size and json.loads(p.sizes_json) and size not in json.loads(p.sizes_json): raise HTTPException(409, 'Invalid size')
                subtotal += Decimal(str(p.sale_price)) * qty
                verified.append((p, size, qty))
            # promo code
            promo_discount = Decimal('0')
            if checkout.promo_code:
                from .models import PromoCode
                from datetime import datetime
                promo = await db.scalar(select(PromoCode).where(PromoCode.code == checkout.promo_code.upper(), PromoCode.active == True))
                if promo and (not promo.expires_at or promo.expires_at >= datetime.now(timezone.utc)) and promo.used_count < promo.max_uses:
                    if subtotal >= promo.min_order:
                        if promo.discount_type == 'percent':
                            promo_discount = subtotal * Decimal(str(promo.discount_value)) / Decimal('100')
                        else:
                            promo_discount = Decimal(str(promo.discount_value))
                        if promo_discount > subtotal:
                            promo_discount = subtotal
                        promo.used_count += 1
            final_total = subtotal - promo_discount
            order = Order(telegram_user_id=telegram_user_id, status='awaiting_payment', subtotal=subtotal, total=final_total, customer_name=checkout.name.strip(), phone=checkout.phone.strip(), city=checkout.city.strip(), address=checkout.address.strip(), comment=checkout.comment.strip() if checkout.comment else None, payment_method=checkout.payment_method)
            db.add(order)
            await db.flush()
            for p, size, qty in verified:
                db.add(OrderItem(order_id=order.id, product_id=p.id, title=p.title, size=size, quantity=qty, unit_price=p.sale_price))
                p.stock -= qty
            order_id = order.id
            # need items for notification after commit
            order_snapshot = {
                "id": order_id,
                "name": checkout.name.strip(),
                "phone": checkout.phone.strip(),
                "city": checkout.city.strip(),
                "address": checkout.address.strip(),
                "comment": checkout.comment.strip() if checkout.comment else "",
                "subtotal": float(subtotal),
                "promo_discount": float(promo_discount),
                "total": float(final_total),
                "tg_id": telegram_user_id,
                "items": [(p.title, size, qty, float(p.sale_price)) for p, size, qty in verified],
            }
        # notify admins outside transaction (fire-and-forget)
        try:
            import asyncio
            from aiogram import Bot
            if settings.admin_ids:
                text = (
                    f"🛒 Новый заказ #{order_snapshot['id']}\n"
                    f"👤 {order_snapshot['name']} | {order_snapshot['phone']}\n"
                    f"📍 {order_snapshot['city']}, {order_snapshot['address']}\n"
                    f"💬 {order_snapshot['comment'] or '-'}\n"
                    f"TG: {order_snapshot['tg_id']}\n"
                    f"Сумма товаров: {order_snapshot['subtotal']:,.0f} ₽\n"
                    + (f"Скидка: -{order_snapshot['promo_discount']:,.0f} ₽\n" if order_snapshot['promo_discount'] else "")
                    + f"Итого: {order_snapshot['total']:,.0f} ₽\n"
                    f"Товары:\n" + "\n".join(f" - {t} {f'({s})' if s else ''} x{qty} = {price:,.0f} ₽" for t, s, qty, price in order_snapshot['items'])
                )
                async def _notify():
                    bot = get_admin_bot()
                    for aid in settings.admin_ids:
                        try:
                            await bot.send_message(aid, text)
                        except Exception:
                            pass
                try:
                    asyncio.create_task(_notify())
                except RuntimeError:
                    # no running loop (tests), run directly
                    pass
        except Exception:
            pass
        # generate Stars invoice link if payment_method == 'stars'
        invoice_link = None
        stars_amount = None
        if checkout.payment_method == 'stars':
            try:
                from aiogram import Bot
                from aiogram.types import LabeledPrice
                bot = get_shop_bot()
                # convert rubles to Stars (1 Star ≈ 2 rubles, min 1)
                stars_amount = max(1, int(float(final_total) / 2))
                prices = [LabeledPrice(label='Заказ', amount=stars_amount)]
                invoice_link = await bot.create_invoice_link(
                    title=f'Заказ #{order_id}',
                    description=f'Оплата заказа #{order_id} в магазине NORMWEAR',
                    payload=f'order_{order_id}',
                    provider_token='',
                    currency='XTR',
                    prices=prices,
                )
            except Exception as e:
                print(f'Stars invoice error: {e}', flush=True)
        return {
            'order_id': order_id,
            'subtotal': order_snapshot['subtotal'],
            'delivery': None,
            'total': float(final_total),
            'stars_amount': stars_amount,
            'invoice_link': invoice_link,
            'status': 'awaiting_payment',
            'message': 'Заказ принят. Стоимость доставки уточнит менеджер.'
        }

# ── POPULAR PRODUCTS ──

@app.get('/api/popular')
async def popular_products():
    from .models import OrderItem
    from sqlalchemy import func
    async with SessionLocal() as db:
        popular = (await db.execute(
            select(OrderItem.product_id, func.count(OrderItem.id).label('cnt'))
            .group_by(OrderItem.product_id)
            .order_by(func.count(OrderItem.id).desc())
            .limit(10)
        )).all()
        product_ids = [r[0] for r in popular]
        if not product_ids:
            return []
        products = {p.id: p for p in (await db.scalars(select(Product).where(Product.id.in_(product_ids), Product.status == 'published'))).all()}
    def _media_urls(p):
        raw = json.loads(p.media_json)
        return [item if item.startswith('http') else f'/media/{p.id}/{i}' for i, item in enumerate(raw)]
    result = []
    for pid, cnt in popular:
        p = products.get(pid)
        if p:
            result.append({'id': p.id, 'title': p.title, 'price': float(p.sale_price), 'media': _media_urls(p), 'orders': cnt})
    return result

# ── ANALYTICS DASHBOARD ──

@app.get('/api/admin/analytics')
async def admin_analytics(request: Request):
    _require_admin(request)
    from .models import OrderItem, Review
    from sqlalchemy import func
    async with SessionLocal() as db:
        total_orders = await db.scalar(select(func.count(Order.id))) or 0
        total_revenue = await db.scalar(select(func.coalesce(func.sum(Order.total), 0))) or 0
        paid_orders = await db.scalar(select(func.count(Order.id)).where(Order.status == 'paid')) or 0
        total_products = await db.scalar(select(func.count(Product.id))) or 0
        published_products = await db.scalar(select(func.count(Product.id)).where(Product.status == 'published')) or 0
        total_users = await db.scalar(select(func.count(func.distinct(Order.telegram_user_id)))) or 0
        total_reviews = await db.scalar(select(func.count(Review.id)).where(Review.status == 'approved')) or 0
        avg_rating = await db.scalar(select(func.coalesce(func.avg(Review.rating), 0)).where(Review.status == 'approved')) or 0
        # top products
        top = (await db.execute(
            select(OrderItem.title, func.count(OrderItem.id).label('cnt'), func.sum(OrderItem.unit_price * OrderItem.quantity).label('rev'))
            .group_by(OrderItem.title)
            .order_by(func.count(OrderItem.id).desc())
            .limit(5)
        )).all()
        # orders by status
        status_counts = (await db.execute(
            select(Order.status, func.count(Order.id))
            .group_by(Order.status)
        )).all()
    return {
        'total_orders': total_orders,
        'total_revenue': float(total_revenue),
        'paid_orders': paid_orders,
        'total_products': total_products,
        'published_products': published_products,
        'total_users': total_users,
        'total_reviews': total_reviews,
        'avg_rating': round(float(avg_rating), 1),
        'top_products': [{'title': r[0], 'orders': r[1], 'revenue': float(r[2] or 0)} for r in top],
        'orders_by_status': {r[0]: r[1] for r in status_counts},
    }

# ── LOW STOCK WARNING (admin notification) ──

@app.post('/api/admin/check-stock')
async def check_low_stock(request: Request):
    _require_admin(request)
    async with SessionLocal() as db:
        low = (await db.scalars(
            select(Product).where(Product.status == 'published', Product.stock <= 2, Product.stock > 0)
        )).all()
    return [{'id': p.id, 'title': p.title, 'stock': p.stock} for p in low]

# ── PUBLISH PRODUCT TO CHANNEL ──

@app.post('/api/admin/publish/{product_id}')
async def publish_to_channel(product_id: int, request: Request):
    _require_admin(request)
    async with SessionLocal() as db:
        p = await db.get(Product, product_id)
        if not p:
            raise HTTPException(404, 'Product not found')
        if float(p.purchase_price) > 0 and float(p.sale_price) <= float(p.purchase_price):
            p.sale_price = float(p.purchase_price) * (1 + settings.default_margin_pct / 100)
            await db.commit()
            await db.refresh(p)
    from .publisher import ChannelPublisher
    pub = ChannelPublisher()
    media = json.loads(p.media_json)
    http_urls = [m for m in media if m.startswith('http')]
    if not http_urls:
        raise HTTPException(400, 'No CDN photos')
    msg_id = await pub.publish(p, http_urls[:6])
    async with SessionLocal() as db:
        p.channel_message_id = msg_id
        await db.commit()
    return {'message_id': msg_id, 'product_id': product_id, 'price': float(p.sale_price)}

# ── LOYALTY ──

@app.get('/api/loyalty')
async def loyalty_balance(request: Request, x_telegram_init_data: str = Header(default='')):
    try:
        init = validate_init_data(x_telegram_init_data)
        user_id = int((init.get('user') or {}).get('id', 0))
    except: raise HTTPException(401, 'Auth required')
    from .models import LoyaltyBalance
    async with SessionLocal() as db:
        bal = await db.scalar(select(LoyaltyBalance).where(LoyaltyBalance.user_telegram_id == user_id))
    if not bal:
        return {'points': 0, 'total_earned': 0, 'total_spent': 0}
    return {'points': bal.points, 'total_earned': bal.total_earned, 'total_spent': bal.total_spent}
