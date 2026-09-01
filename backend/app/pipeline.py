from __future__ import annotations
import json, os
from decimal import Decimal
from sqlalchemy import select
from .db import SessionLocal
from .models import Product
from .supplier import SupplierWorker
from .pricing import recommend_price
from .market import MarketProvider, summarize
from .config import settings

async def ingest_supplier(days: int = 14, provider: MarketProvider | None = None) -> dict:
    worker = SupplierWorker()
    items = await worker.extract_products(days)
    created = 0
    skipped = 0
    provider = provider or MarketProvider()
    published = 0
    async with SessionLocal() as db:
        for parsed, media, post in items:
            exists = await db.scalar(select(Product).where(Product.supplier_message_id == post.message_id))
            if exists:
                skipped += 1
                continue
            offers = await provider.search(parsed.brand, parsed.model, parsed.title)
            market = summarize(offers)
            decision = recommend_price(
                Decimal(str(parsed.purchase_price)),
                Decimal(str(settings.default_margin_pct)),
                market,
            )
            product = Product(
                supplier_chat='@' + settings.supplier_channel_username,
                supplier_message_id=post.message_id,
                supplier_grouped_id=post.grouped_id,
                title=parsed.title,
                description=parsed.description,
                category=parsed.category,
                sizes_json=json.dumps(parsed.sizes, ensure_ascii=False),
                media_json=json.dumps([m.file_path for m in media], ensure_ascii=False),
                purchase_price=parsed.purchase_price,
                sale_price=decision.price,
                price_confidence=decision.confidence,
                stock=parsed.stock,
                status='approved' if settings.auto_publish and decision.confidence >= settings.price_review_threshold else 'pending',
            )
            db.add(product)
            created += 1
        await db.commit()

    if settings.auto_publish:
        from .publisher import ChannelPublisher
        async with SessionLocal() as db:
            auto_rows = (await db.scalars(select(Product).where(Product.status == 'approved', Product.channel_message_id.is_(None)).order_by(Product.id))).all()
            publisher = ChannelPublisher()
            for product in auto_rows:
                try:
                    mid = await publisher.publish(product, json.loads(product.media_json))
                    product.status = 'published'
                    product.channel_message_id = mid
                    published += 1
                except Exception:
                    # Keep approved; next sync can retry.
                    continue
            await db.commit()
    return {'created': created, 'skipped': skipped, 'published': published}
