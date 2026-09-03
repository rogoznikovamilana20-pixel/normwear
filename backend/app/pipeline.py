from __future__ import annotations
import json, hashlib
from datetime import datetime, timezone
from sqlalchemy import select
from .db import SessionLocal
from .models import Product, BannedProduct, SourcePost, ProcessingJob
from .supplier import SupplierWorker
from .parser import parse_product
from .services import select_product_media
from .config import settings

async def ingest_supplier(days: int = 14) -> dict:
    worker = SupplierWorker()
    posts = await worker.collect_recent_posts(days)
    created = 0
    skipped = 0
    published = 0
    errors = 0

    banned_skus: set[str] = set()
    banned_patterns: list[str] = []

    async with SessionLocal() as db:
        for b in (await db.scalars(select(BannedProduct))).all():
            if b.sku:
                banned_skus.add(b.sku)
            if b.title_pattern:
                banned_patterns.append(b.title_pattern.lower())

    async with SessionLocal() as db:
        for post in posts:
            try:
                parsed = parse_product(post.text)
                if not parsed:
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=len(post.media),
                        processing_status='skipped_no_product',
                    )
                    db.add(sp)
                    skipped += 1
                    continue

                media = select_product_media(post)
                if not media:
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=0,
                        processing_status='skipped_no_media',
                    )
                    db.add(sp)
                    skipped += 1
                    continue

                if parsed.article and parsed.article in banned_skus:
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=len(media),
                        processing_status='skipped_banned',
                    )
                    db.add(sp)
                    skipped += 1
                    continue

                if any(pat in parsed.title.lower() for pat in banned_patterns):
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=len(media),
                        processing_status='skipped_banned_pattern',
                    )
                    db.add(sp)
                    skipped += 1
                    continue

                exists = await db.scalar(
                    select(Product).where(Product.supplier_message_id == post.message_id)
                )
                if exists:
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=len(media),
                        product_id=exists.id,
                        processing_status='skipped_duplicate',
                    )
                    db.add(sp)
                    skipped += 1
                    continue

                dup = await db.scalar(
                    select(Product).where(
                        Product.title == parsed.title,
                        Product.purchase_price == parsed.purchase_price,
                    )
                )
                if dup:
                    sp = SourcePost(
                        source_channel='@' + settings.supplier_channel_username,
                        source_message_id=post.message_id,
                        source_album_id=post.grouped_id,
                        raw_text=post.text,
                        media_count=len(media),
                        product_id=dup.id,
                        processing_status='skipped_duplicate_product',
                    )
                    db.add(sp)
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

                sp = SourcePost(
                    source_channel='@' + settings.supplier_channel_username,
                    source_message_id=post.message_id,
                    source_album_id=post.grouped_id,
                    raw_text=post.text,
                    media_count=len(media),
                    product_id=product.id,
                    product_hash=hashlib.sha256(f"{parsed.title}|{parsed.purchase_price}".encode()).hexdigest()[:32],
                    processing_status='processed',
                )
                db.add(sp)
                job = ProcessingJob(
                    job_type='ingest_post',
                    source_post_id=sp.id,
                    status='success',
                    payload_json=json.dumps({'message_id': post.message_id, 'title': parsed.title}),
                    result_json=json.dumps({'product_id': product.id, 'price': float(sale_price)}),
                )
                db.add(job)
                created += 1

                if settings.auto_publish and product.status == 'published':
                    try:
                        from .publisher import ChannelPublisher
                        pub = ChannelPublisher()
                        all_media = json.loads(product.media_json)
                        http_urls = [m for m in all_media if m.startswith('http')]
                        local_paths = [m for m in all_media if not m.startswith('http')]
                        msg_id = await pub.publish(product, http_urls[:6] if http_urls else local_paths[:6])
                        product.channel_message_id = msg_id
                        published += 1
                    except Exception as e:
                        print(f'[pipeline] publish error {product.id}: {e}', flush=True)

            except Exception as e:
                print(f'[pipeline] error post {post.message_id}: {e}', flush=True)
                errors += 1
                sp = SourcePost(
                    source_channel='@' + settings.supplier_channel_username,
                    source_message_id=post.message_id,
                    source_album_id=post.grouped_id,
                    raw_text=(post.text or '')[:1000],
                    media_count=len(post.media),
                    processing_status=f'error: {str(e)[:200]}',
                )
                db.add(sp)

        await db.commit()

    return {'created': created, 'skipped': skipped, 'published': published, 'errors': errors, 'total_posts': len(posts)}
