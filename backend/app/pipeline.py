from __future__ import annotations
import json, hashlib
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import select, func
from .db import SessionLocal
from .models import Product, BannedProduct, SourcePost, ProcessingJob
from .supplier import SupplierWorker
from .parser import parse_product
from .services import select_product_media
from .config import settings

def _product_hash(title: str, price: float, sizes: list[str]) -> str:
    raw = f"{title.lower().strip()}|{price}|{','.join(sorted(sizes))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

async def _get_banned(db) -> tuple[set[str], list[str]]:
    banned_skus: set[str] = set()
    banned_patterns: list[str] = []
    for b in (await db.scalars(select(BannedProduct))).all():
        if b.sku:
            banned_skus.add(b.sku)
        if b.title_pattern:
            banned_patterns.append(b.title_pattern.lower())
    return banned_skus, banned_patterns

async def _upsert_source_post(db, post, text: str, media_count: int, product_id: int | None, status: str) -> SourcePost:
    existing = await db.scalar(
        select(SourcePost).where(
            SourcePost.source_channel == '@' + settings.supplier_channel_username,
            SourcePost.source_message_id == post.message_id,
        )
    )
    if existing:
        existing.raw_text = text
        existing.media_count = media_count
        existing.product_id = product_id or existing.product_id
        existing.processing_status = status
        existing.updated_at = datetime.now(timezone.utc)
        return existing
    sp = SourcePost(
        source_channel='@' + settings.supplier_channel_username,
        source_message_id=post.message_id,
        source_album_id=post.grouped_id,
        raw_text=text,
        media_count=media_count,
        product_id=product_id,
        processing_status=status,
    )
    db.add(sp)
    return sp

async def ingest_supplier(days: int = 14) -> dict:
    worker = SupplierWorker()
    posts = await worker.collect_recent_posts(days)
    created = 0
    skipped = 0
    published = 0
    errors = 0

    async with SessionLocal() as db:
        banned_skus, banned_patterns = await _get_banned(db)

    for post in posts:
        try:
            parsed = parse_product(post.text)
            if not parsed:
                async with SessionLocal() as db:
                    await _upsert_source_post(db, post, post.text, len(post.media), None, 'skipped_no_product')
                    await db.commit()
                skipped += 1
                continue

            media = select_product_media(post)
            if not media:
                async with SessionLocal() as db:
                    await _upsert_source_post(db, post, post.text, 0, None, 'skipped_no_media')
                    await db.commit()
                skipped += 1
                continue

            if parsed.article and parsed.article in banned_skus:
                async with SessionLocal() as db:
                    await _upsert_source_post(db, post, post.text, len(media), None, 'skipped_banned')
                    await db.commit()
                skipped += 1
                continue

            if any(pat in parsed.title.lower() for pat in banned_patterns):
                async with SessionLocal() as db:
                    await _upsert_source_post(db, post, post.text, len(media), None, 'skipped_banned_pattern')
                    await db.commit()
                skipped += 1
                continue

            phash = _product_hash(parsed.title, parsed.purchase_price, parsed.sizes)

            async with SessionLocal() as db:
                exists = await db.scalar(
                    select(Product).where(Product.supplier_message_id == post.message_id)
                )
                if exists:
                    await _upsert_source_post(db, post, post.text, len(media), exists.id, 'skipped_duplicate')
                    await db.commit()
                    skipped += 1
                    continue

                dup_hash = await db.scalar(
                    select(Product).where(
                        Product.title == parsed.title,
                        Product.purchase_price == parsed.purchase_price,
                    )
                )
                if dup_hash:
                    await _upsert_source_post(db, post, post.text, len(media), dup_hash.id, 'skipped_duplicate_product')
                    await db.commit()
                    skipped += 1
                    continue

                sale_price = round(parsed.purchase_price * (1 + settings.default_margin_pct / 100))
                product = Product(
                    supplier_chat='@' + settings.supplier_channel_username,
                    supplier_message_id=post.message_id,
                    supplier_grouped_id=post.grouped_id,
                    sku=parsed.article,
                    brand=parsed.brand,
                    model=parsed.model,
                    title=parsed.title,
                    description=parsed.description,
                    category=parsed.category,
                    sizes_json=json.dumps(parsed.sizes, ensure_ascii=False),
                    media_json=json.dumps([m.file_path for m in media], ensure_ascii=False),
                    purchase_price=parsed.purchase_price,
                    sale_price=sale_price,
                    price_confidence=0.8,
                    stock=parsed.stock,
                    status='published' if settings.auto_publish else 'pending',
                )
                db.add(product)
                await db.flush()

                sp = await _upsert_source_post(db, post, post.text, len(media), product.id, 'processed')
                job = ProcessingJob(
                    job_type='ingest_post',
                    source_post_id=sp.id,
                    status='success',
                    payload_json=json.dumps({'message_id': post.message_id, 'title': parsed.title}),
                    result_json=json.dumps({'product_id': product.id, 'price': float(sale_price)}),
                )
                db.add(job)
                await db.commit()
                created += 1

                if settings.auto_publish and product.status == 'published':
                    try:
                        from .publisher import ChannelPublisher
                        pub = ChannelPublisher()
                        http_urls = [m for m in json.loads(product.media_json) if m.startswith('http')]
                        local_paths = [m for m in json.loads(product.media_json) if not m.startswith('http')]
                        msg_id = await pub.publish(product, http_urls[:6] if http_urls else local_paths[:6])
                        product.channel_message_id = msg_id
                        await db.commit()
                        published += 1
                    except Exception as e:
                        print(f'[pipeline] publish error {product.id}: {e}', flush=True)

        except Exception as e:
            print(f'[pipeline] error post {post.message_id}: {e}', flush=True)
            errors += 1
            try:
                async with SessionLocal() as db:
                    await _upsert_source_post(db, post, post.text, len(post.media), None, f'error: {str(e)[:200]}')
                    await db.commit()
            except Exception:
                pass

    return {'created': created, 'skipped': skipped, 'published': published, 'errors': errors, 'total_posts': len(posts)}
