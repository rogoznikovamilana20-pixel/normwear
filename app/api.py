import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.config import BASE_DIR, get_settings
from app.db import SessionMaker
from app.models import Brand, CartItem, Product, ProductPhoto
from app.services.photos import yandex_library

settings = get_settings()
PAGE = 12


def create_app() -> FastAPI:
    app = FastAPI(title="NORMWEAR API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/api/config")
    async def api_config():
        return {"shop_username": settings.shop_username, "channel_username": settings.shop_channel_username}

    @app.get("/api/brands")
    async def api_brands():
        async with SessionMaker() as s:
            rows = (
                await s.execute(
                    select(Brand.id, Brand.title, func.count(Product.id))
                    .join(Product, Product.brand_id == Brand.id)
                    .where(Product.status == "published")
                    .group_by(Brand.id)
                    .order_by(Brand.title)
                )
            ).all()
        return [{"id": r[0], "title": r[1], "count": r[2]} for r in rows]

    @app.get("/api/catalog")
    async def api_catalog(brand: int | None = None, page: int = 0, sort: str = "new"):
        page = max(0, page)
        order = Product.id.desc()
        if sort == "price_asc":
            order = Product.retail_price.asc()
        elif sort == "price_desc":
            order = Product.retail_price.desc()
        q = select(Product).where(Product.status == "published").order_by(order)
        if brand:
            q = q.where(Product.brand_id == brand)
        async with SessionMaker() as s:
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
            prods = (await s.scalars(q.limit(PAGE).offset(page * PAGE))).all()
            # отдаём прокси-URL: сервер сам разрулит источник и закэширует
            async def _first_photo(pid: int):
                has = (
                    await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid).limit(1))
                ).first()
                return f"/img/{pid}" if has else None

            # делаем запросы последовательно внутри одной сессии (параллель внутри сессии нежелательна),
            # но каждый download_url — внешний HTTP, делаем с таймаутом чтобы не вешать ответ
            out = []
            for p in prods:
                try:
                    photo = await _first_photo(p.id)
                except Exception:
                    photo = None
                out.append({"id": p.id, "title": p.title, "price": int(p.retail_price or 0), "sizes": p.sizes or [], "photo": photo})
        return {"total": total, "page": page, "pages": (total + PAGE - 1) // PAGE, "items": out}

    async def _serve_bytes(pid: int, idx: int) -> tuple[bytes, str] | None:
        """Байты фото товара: локальный кэш -> supplier http -> yandex. Кэшируем на диск."""
        import os

        from fastapi.responses import Response  # noqa

        from app.services.photos import resolve_local

        cache = BASE_DIR / "data" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / f"{pid}_{idx}.jpg"
        if cached.exists() and cached.stat().st_size > 2048:
            return cached.read_bytes(), "image/jpeg"
        import httpx as _httpx

        async with SessionMaker() as s:
            p = await s.get(Product, pid)
            if p is None or p.status != "published":
                return None
            photos = (
                await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid).order_by(ProductPhoto.position))
            ).all()
        # порядок: локальное -> http -> yandex
        ordered = sorted(photos, key=lambda ph: {"local": 0, "supplier": 1, "yandex": 2}.get(ph.source, 3))
        if idx < len(ordered):
            ordered = ordered[idx:] + ordered[:idx]
        async with _httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
            for ph in ordered:
                try:
                    data = None
                    if ph.source == "local":
                        real = resolve_local(ph.url)
                        if real:
                            data = open(real, "rb").read()
                    elif ph.source == "supplier" and isinstance(ph.url, str) and ph.url.startswith("http"):
                        r = await c.get(ph.url)
                        if r.status_code == 200 and (r.headers.get("content-type", "").startswith("image")):
                            data = r.content
                    elif ph.source == "supplier" and isinstance(ph.url, str) and ph.url:
                        # file_id админ-бота: качаем через него и кэшируем
                        from app.services import notify as _notify

                        if _notify.admin_bot is not None:
                            f = await _notify.admin_bot.get_file(ph.url)
                            buf = await _notify.admin_bot.download_file(f.file_path)
                            raw = buf.read()
                            if raw and len(raw) > 2048:
                                data = raw
                    elif ph.source == "yandex":
                        href = await asyncio.wait_for(yandex_library.download_url(ph.url), timeout=5.0)
                        if href:
                            # без заголовка Referer вообще — Яндекс отдаёт 200, с любым Referer 403
                            r = await c.get(href)
                            if r.status_code == 200 and (r.headers.get("content-type", "").startswith("image")):
                                data = r.content
                    if data and len(data) > 2048:
                        cached.write_bytes(data)
                        return data, "image/jpeg"
                except Exception:
                    continue
        return None

    @app.get("/img/{pid}")
    async def img_cover(pid: int):
        from fastapi.responses import Response

        got = await _serve_bytes(pid, 0)
        if got is None:
            raise HTTPException(status_code=404, detail="no photo")
        return Response(content=got[0], media_type=got[1])

    @app.get("/img/{pid}/{idx}")
    async def img_idx(pid: int, idx: int):
        from fastapi.responses import Response

        got = await _serve_bytes(pid, max(0, idx))
        if got is None:
            raise HTTPException(status_code=404, detail="no photo")
        return Response(content=got[0], media_type=got[1])

    @app.get("/api/product/{pid}")
    async def api_product(pid: int):
        async with SessionMaker() as s:
            p = await s.get(Product, pid)
            if p is None or p.status != "published":
                raise HTTPException(status_code=404, detail="product not found")
            brand = await s.get(Brand, p.brand_id) if p.brand_id else None
            n = len(
                (await s.scalars(select(ProductPhoto).where(ProductPhoto.product_id == pid))).all()
            )
        return {
            "id": p.id,
            "title": p.title,
            "brand": brand.title if brand else None,
            "price": int(p.retail_price or 0),
            "sizes": p.sizes or [],
            "photos": [f"/img/{pid}/{i}" for i in range(min(n, 10))],
        }

    @app.get("/api/cart")
    async def api_get_cart(user_id: int):
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required")
        async with SessionMaker() as s:
            items = (
                await s.execute(
                    select(CartItem, Product)
                    .join(Product, CartItem.product_id == Product.id)
                    .where(CartItem.user_id == user_id)
                    .order_by(CartItem.id)
                )
            ).all()
            out = []
            total = 0
            for ci, p in items:
                price = int(p.retail_price or 0)
                total += price * ci.qty
                out.append({"cart_id": ci.id, "product_id": p.id, "title": p.title, "price": price, "size": ci.size, "qty": ci.qty})
        return {"items": out, "total": total, "count": len(out)}

    @app.get("/api/orders")
    async def api_my_orders(user_id: int):
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required")
        from app.models import Order

        labels = {
            "awaiting_delivery": "Ждёт поступления",
            "awaiting_payment": "К оплате",
            "shipped": "Отправлен",
            "delivered": "Доставлен",
            "completed": "Завершён",
            "cancelled": "Отменён",
        }
        async with SessionMaker() as s:
            ords = (
                await s.scalars(select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(10))
            ).all()
        return [
            {"id": o.id, "status": o.status, "label": labels.get(o.status, o.status), "total": int(o.total), "date": o.created_at.strftime("%d.%m.%Y") if o.created_at else ""}
            for o in ords
        ]

    @app.delete("/api/cart/{cart_id}")
    async def api_del_cart(cart_id: int, user_id: int):
        if not cart_id or not user_id:
            raise HTTPException(status_code=400, detail="bad payload")
        async with SessionMaker() as s:
            ci = await s.get(CartItem, cart_id)
            if ci is None or ci.user_id != user_id:
                raise HTTPException(status_code=404, detail="not found")
            await s.delete(ci)
            await s.commit()
        return {"ok": True}

    @app.post("/api/cart")
    async def api_add_cart(payload: dict):
        try:
            user_id = int(payload.get("user_id") or 0)
            pid = int(payload.get("product_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bad payload")
        size = str(payload.get("size") or "")[:16]
        if not user_id or not pid:
            raise HTTPException(status_code=400, detail="user_id and product_id required")
        async with SessionMaker() as s:
            p = await s.get(Product, pid)
            if p is None or p.status != "published":
                raise HTTPException(status_code=404, detail="product not found")
            existing = (
                await s.scalars(
                    select(CartItem).where(CartItem.user_id == user_id, CartItem.product_id == pid, CartItem.size == size)
                )
            ).first()
            if existing is None:
                s.add(CartItem(user_id=user_id, product_id=pid, size=size))
                await s.commit()
        return {"ok": True}

    @app.post("/api/order")
    async def api_create_order(payload: dict):
        try:
            user_id = int(payload.get("user_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bad user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required")
        from app.models import User

        async with SessionMaker() as s:
            user = await s.get(User, user_id)
            if user is None:
                # создаём заглушку если юзера нет (для теста)
                user = User(id=user_id, username=None, first_name="WebApp")
                s.add(user)
                await s.flush()
            data = {
                "full_name": str(payload.get("full_name") or "")[:120],
                "phone": str(payload.get("phone") or "")[:30],
                "city": str(payload.get("city") or "")[:60],
                "address": str(payload.get("address") or "")[:250],
                "promo_code": str(payload.get("promo_code") or "")[:32] or None,
                "use_bonus": bool(payload.get("use_bonus")),
            }
            if len(data["full_name"]) < 2 or len(data["phone"]) < 10 or len(data["city"]) < 2 or len(data["address"]) < 5:
                raise HTTPException(status_code=400, detail="заполни имя/телефон/город/адрес")
            from app.services import orders as orders_svc
            from app.services import notify as notify_svc
            import html as _html
            from app.models import OrderItem
            from sqlalchemy import select as _select

            order, err = await orders_svc.create_order(s, user, data)
            if order is None:
                raise HTTPException(status_code=400, detail=err or "не удалось создать заказ")
            # уведомляем админов
            try:
                items = (await s.scalars(_select(OrderItem).where(OrderItem.order_id == order.id))).all()
                lines = [f"• {_html.escape(it.title)}{(' · ' + it.size) if it.size else ''} — {int(it.price)}₽" for it in items]
                uname = f"@{user.username}" if user.username else "без юзернейма"
                admin_text = (
                    f"🆕 <b>Новый заказ №{order.id} (мини-апп)</b>\n"
                    f"👤 {_html.escape(order.full_name)} · {uname} · id <code>{order.user_id}</code>\n"
                    f"📱 {_html.escape(order.phone)}\n"
                    f"🏙 {_html.escape(order.city)}, {_html.escape(order.address)}\n\n"
                    + "\n".join(lines)
                    + f"\n\n💰 <b>Итого: {int(order.total)} ₽</b>"
                )
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="📦 Открыть заказ", callback_data=f"od:{order.id}")],
                        [InlineKeyboardButton(text="➡ Готов к оплате", callback_data=f"od:{order.id}:next")],
                    ]
                )
                await notify_svc.notify_admins(admin_text, kb)
            except Exception:
                pass
            return {"ok": True, "order_id": order.id, "total": int(order.total)}

    @app.get("/api/stats")
    async def api_stats():
        from app.models import Order, User, Review

        async with SessionMaker() as s:
            users = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
            prods = (await s.execute(select(func.count()).select_from(Product).where(Product.status == "published"))).scalar() or 0
            orders_total = (await s.execute(select(func.count()).select_from(Order))).scalar() or 0
            orders_completed = (await s.execute(select(func.count()).select_from(Order).where(Order.status == "completed"))).scalar() or 0
            revenue = (await s.execute(select(func.coalesce(func.sum(Order.total), 0.0)).where(Order.status == "completed"))).scalar() or 0
            reviews = (await s.execute(select(func.count()).select_from(Review))).scalar() or 0
            # топ бренды по заказам
            # конверсия: пользователи с заказами / все
            users_with_orders = (await s.execute(select(func.count(func.distinct(Order.user_id))))).scalar() or 0
        conv = round(users_with_orders / users * 100, 1) if users else 0
        return {
            "users": users,
            "products": prods,
            "orders_total": orders_total,
            "orders_completed": orders_completed,
            "revenue": int(revenue),
            "reviews": reviews,
            "conversion": conv,
        }

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    media_dir = BASE_DIR / "data" / "supplier"
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
    return app