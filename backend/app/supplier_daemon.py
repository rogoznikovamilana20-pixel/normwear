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

async def _process_retry_jobs():
    from .db import SessionLocal
    from .models import ProcessingJob, SourcePost
    from sqlalchemy import select
    from .pipeline import _product_hash
    from .parser import parse_product
    from .services import select_product_media
    from .config import settings
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        pending = (await db.scalars(
            select(ProcessingJob).where(
                ProcessingJob.status == 'retry',
                ProcessingJob.next_retry_at <= now,
                ProcessingJob.attempts < ProcessingJob.max_attempts,
            ).order_by(ProcessingJob.id).limit(10)
        )).all()
        for job in pending:
            job.attempts += 1
            job.status = 'processing'
            try:
                if job.job_type == 'ingest_post' and job.source_post_id:
                    sp = await db.get(SourcePost, job.source_post_id)
                    if sp and sp.raw_text:
                        parsed = parse_product(sp.raw_text)
                        if parsed:
                            from .models import Product
                            sale_price = round(parsed.purchase_price * (1 + settings.default_margin_pct / 100))
                            product = Product(
                                supplier_chat=sp.source_channel,
                                supplier_message_id=sp.source_message_id,
                                sku=parsed.article,
                                brand=parsed.brand,
                                title=parsed.title,
                                description=parsed.description,
                                category=parsed.category,
                                sizes_json=json.dumps(parsed.sizes, ensure_ascii=False),
                                media_json='[]',
                                purchase_price=parsed.purchase_price,
                                sale_price=sale_price,
                                stock=parsed.stock,
                                status='published' if settings.auto_publish else 'pending',
                            )
                            db.add(product)
                            await db.flush()
                            sp.product_id = product.id
                            sp.processing_status = 'processed'
                            job.status = 'success'
                            job.result_json = json.dumps({'product_id': product.id})
                        else:
                            job.status = 'failed'
                            job.error_message = 'parse returned None'
                    else:
                        job.status = 'failed'
                        job.error_message = 'source_post not found or empty'
                else:
                    job.status = 'failed'
                    job.error_message = f'unknown job_type: {job.job_type}'
            except Exception as e:
                job.status = 'retry'
                job.error_message = str(e)[:500]
                backoff = min(300, 30 * (2 ** (job.attempts - 1)))
                job.next_retry_at = now + timedelta(seconds=backoff)
            if job.attempts >= job.max_attempts and job.status != 'success':
                job.status = 'failed'
        await db.commit()

async def main():
    first = True
    reminder_tick = 0
    stock_tick = 0
    retry_tick = 0
    while True:
        try:
            result = await ingest_supplier(days=7 if first else 1)
            print(f'[daemon] sync: {result}', flush=True)
            first = False
        except Exception as exc:
            print(f'[daemon] sync error: {exc!r}', flush=True)
        reminder_tick += 1
        if reminder_tick >= 10:
            reminder_tick = 0
            try:
                await _check_cart_reminders()
            except Exception as exc:
                print(f'[daemon] reminder error: {exc!r}', flush=True)
        retry_tick += 1
        if retry_tick >= 5:
            retry_tick = 0
            try:
                await _process_retry_jobs()
            except Exception as exc:
                print(f'[daemon] retry error: {exc!r}', flush=True)
        stock_tick += 1
        if stock_tick >= 30:
            stock_tick = 0
        await asyncio.sleep(60)

if __name__ == '__main__':
    asyncio.run(main())
