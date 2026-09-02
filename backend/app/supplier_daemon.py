import asyncio, json
from datetime import datetime, timedelta, timezone
from .pipeline import ingest_supplier

async def _check_cart_reminders():
    from .db import SessionLocal
    from .models import CartReminder, Product
    from .config import settings, get_shop_bot
    from sqlalchemy import select
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    async with SessionLocal() as db:
        stale = (await db.scalars(
            select(CartReminder).where(CartReminder.reminded == False, CartReminder.created_at < cutoff)
        )).all()
        for cr in stale:
            cr.reminded = True
            product_ids = json.loads(cr.product_ids_json)
            products = []
            for pid in product_ids[:3]:
                p = await db.get(Product, pid)
                if p:
                    products.append(p)
            if products:
                lines = [f"• {p.title} — {float(p.sale_price):,.0f} ₽" for p in products]
                text = "🛒 <b>Вы забыли товары в корзине!</b>\n\n" + "\n".join(lines)
                try:
                    bot = get_shop_bot()
                    await bot.send_message(cr.user_telegram_id, text, parse_mode='HTML')
                except Exception:
                    pass
        await db.commit()

async def _sync_stock_levels():
    """Sync stock levels: if supplier has no post, mark out of stock."""
    from .db import SessionLocal
    from .models import Product
    from sqlalchemy import select, update
    try:
        async with SessionLocal() as db:
            # Mark products with 0 supplier message as potentially out of stock
            # This is a simple heuristic — in reality you'd parse supplier catalog
            result = await db.execute(
                update(Product)
                .where(Product.supplier_message_id == 0, Product.status == 'published')
                .values(status='draft')
            )
            if result.rowcount > 0:
                await db.commit()
                print(f'Stock sync: {result.rowcount} products marked draft (no supplier data)', flush=True)
    except Exception as exc:
        print(f'Stock sync error: {exc!r}', flush=True)

async def main():
    first = True
    reminder_tick = 0
    stock_tick = 0
    while True:
        try:
            result = await ingest_supplier(days=7 if first else 1)
            print(result, flush=True)
            first = False
        except Exception as exc:
            print(f"supplier sync error: {exc!r}", flush=True)
        reminder_tick += 1
        if reminder_tick >= 10:
            reminder_tick = 0
            try:
                await _check_cart_reminders()
            except Exception as exc:
                print(f"cart reminder error: {exc!r}", flush=True)
        stock_tick += 1
        if stock_tick >= 30:
            stock_tick = 0
            try:
                await _sync_stock_levels()
            except Exception as exc:
                print(f"stock sync error: {exc!r}", flush=True)
        await asyncio.sleep(60)

if __name__ == '__main__': asyncio.run(main())
