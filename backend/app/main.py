import json
from decimal import Decimal
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
from .config import settings

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

app = FastAPI(title='NORMWEAR Commerce API', version='1.0.0')
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
import os as _os, asyncio
for _p in ('/app/miniapp/dist', '/app/miniapp', 'miniapp/dist', 'miniapp', '../miniapp/dist', '../miniapp'):
    if _os.path.isdir(_p):
        app.mount('/app', StaticFiles(directory=_p, html=True), name='miniapp')
        break

@app.on_event("startup")
async def _startup_bots():
    # run shop/admin bots and supplier daemon inside same process (Render free single web)
    try:
        from .bot_shop import dp as shop_dp
        from .bot_admin import dp as admin_dp
        from aiogram import Bot
        print("[startup] imports OK", flush=True)
        async def _run_shop():
            try:
                bot = Bot(settings.shop_bot_token)
                print("[shop_bot] starting polling", flush=True)
                await shop_dp.start_polling(bot)
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"[shop_bot] FATAL {e}", flush=True)
        async def _run_admin():
            try:
                bot = Bot(settings.admin_bot_token)
                print("[admin_bot] starting polling", flush=True)
                await admin_dp.start_polling(bot)
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"[admin_bot] FATAL {e}", flush=True)
        async def _run_supplier():
            try:
                from .supplier_daemon import main as sup_main
                print("[supplier] starting", flush=True)
                await sup_main()
            except Exception as e:
                print(f"[supplier] error {e}", flush=True)
        # only start if tokens look valid
        if settings.shop_bot_token and len(settings.shop_bot_token) > 20:
            asyncio.create_task(_run_shop())
        if settings.admin_bot_token and len(settings.admin_bot_token) > 20:
            asyncio.create_task(_run_admin())
        # supplier needs mtproto, but webscrape fallback works without
        asyncio.create_task(_run_supplier())
        print("[startup] bots+supplier scheduled", flush=True)
    except Exception as e:
        print(f"[startup] failed {e}", flush=True)

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
    data = [{
        'id': p.id, 'title': p.title, 'description': p.description or '', 'category': p.category,
        'price': float(p.sale_price), 'stock': p.stock, 'sizes': json.loads(p.sizes_json),
        'media': [f'/media/{p.id}/{i}' for i, _ in enumerate(json.loads(p.media_json))]
    } for p in rows]
    return JSONResponse(content=data, headers={"X-Total-Count": str(total), "X-Limit": str(limit), "X-Offset": str(offset)})

@app.get('/api/products/{product_id}')
async def product(product_id: int):
    async with SessionLocal() as db: p = await db.get(Product, product_id)
    if not p or p.status not in {'published','approved'}: raise HTTPException(404, 'Product not found')
    return {'id':p.id,'title':p.title,'description':p.description or '', 'category':p.category,'price':float(p.sale_price),'stock':p.stock,'sizes':json.loads(p.sizes_json),'media':[f'/media/{p.id}/{i}' for i, _ in enumerate(json.loads(p.media_json))]}

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
            return {'id': p.id, 'title': p.title}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={'detail': str(e)})

@app.post('/api/products/bulk-publish')
@limiter.limit("5/minute")
async def bulk_publish(request: Request):
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
        if p.expires_at and p.expires_at < datetime.utcnow(): raise HTTPException(400, 'Промокод истёк')
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
                if promo and (not promo.expires_at or promo.expires_at >= datetime.utcnow()) and promo.used_count < promo.max_uses:
                    if subtotal >= promo.min_order:
                        if promo.discount_type == 'percent':
                            promo_discount = subtotal * Decimal(str(promo.discount_value)) / Decimal('100')
                        else:
                            promo_discount = Decimal(str(promo.discount_value))
                        if promo_discount > subtotal:
                            promo_discount = subtotal
                        promo.used_count += 1
            final_total = subtotal - promo_discount
            order = Order(telegram_user_id=telegram_user_id, status='awaiting_delivery', subtotal=subtotal, total=final_total, customer_name=checkout.name.strip(), phone=checkout.phone.strip(), city=checkout.city.strip(), address=checkout.address.strip(), comment=checkout.comment.strip() if checkout.comment else None, payment_method=checkout.payment_method)
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
                    bot = Bot(settings.admin_bot_token)
                    for aid in settings.admin_ids:
                        try:
                            await bot.send_message(aid, text)
                        except Exception:
                            pass
                    try:
                        await bot.session.close()
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
        if checkout.payment_method == 'stars':
            try:
                from aiogram import Bot
                from aiogram.types import LabeledPrice
                bot = Bot(settings.shop_bot_token)
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
                await bot.session.close()
            except Exception as e:
                print(f'Stars invoice error: {e}', flush=True)
        return {
            'order_id': order_id,
            'subtotal': order_snapshot['subtotal'],
            'delivery': None,
            'total': None,
            'stars_amount': stars_amount if checkout.payment_method == 'stars' else None,
            'invoice_link': invoice_link,
            'status': 'awaiting_delivery',
            'message': 'Заказ принят. Стоимость доставки уточнит менеджер.'
        }
