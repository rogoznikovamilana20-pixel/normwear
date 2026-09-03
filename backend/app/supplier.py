from __future__ import annotations
import json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from .config import settings
from .parser import parse_product
from .services import MediaItem, SupplierPost, select_product_media

MEDIA_DIR = Path('media/supplier')

# lazy telethon import only if keys present
def _has_mtproto() -> bool:
    return bool(settings.telegram_api_id and settings.telegram_api_hash and settings.supplier_session_string)

class SupplierWorker:
    def __init__(self):
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.client = None
        if _has_mtproto():
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            self.client = TelegramClient(StringSession(settings.supplier_session_string), settings.telegram_api_id, settings.telegram_api_hash)

    async def _collect_mtproto(self, cutoff) -> list[SupplierPost]:
        await self.client.start()
        groups: dict[int, SupplierPost] = {}
        singles: list[SupplierPost] = []
        async for msg in self.client.iter_messages(settings.supplier_channel_username, reverse=False):
            dt = msg.date
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt and dt < cutoff:
                break
            text = msg.message or ''
            grouped_id = getattr(msg, 'grouped_id', None)
            target = groups.get(grouped_id) if grouped_id else None
            if target is None:
                target = SupplierPost(msg.id, grouped_id, text, [])
                if grouped_id:
                    groups[grouped_id] = target
                else:
                    singles.append(target)
            elif text and not target.text:
                target.text = text
            if msg.media:
                path = MEDIA_DIR / f'{msg.id}.bin'
                try:
                    saved = await self.client.download_media(msg, file=str(path))
                    if saved:
                        mime = getattr(getattr(msg, 'file', None), 'mime_type', None)
                        target.media.append(MediaItem(msg.id, str(saved), mime))
                except Exception as e:
                    print(f'[supplier] download error msg {msg.id}: {e}', flush=True)
        result = singles + list(groups.values())
        result.sort(key=lambda x: x.message_id)
        return result

    async def _collect_webscrape(self, days: int) -> list[SupplierPost]:
        # fallback without API keys: scrape https://t.me/s/optobaza
        import httpx
        from bs4 import BeautifulSoup
        url = f"https://t.me/s/{settings.supplier_channel_username}"
        posts: list[SupplierPost] = []
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(url, headers={"User-Agent":"Mozilla/5.0"})
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                for idx, wrap in enumerate(soup.select(".tgme_widget_message_wrap")):
                    text_el = wrap.select_one(".tgme_widget_message_text")
                    text = text_el.get_text("\n", strip=True) if text_el else ""
                    # try extract date
                    time_el = wrap.select_one("time")
                    # keep all recent, filter by parse_product later; keep last 30
                    media_items: list[MediaItem] = []
                    for a in wrap.select("a.tgme_widget_message_photo_wrap"):
                        style = a.get("style","")
                        m = re.search(r"url\('([^']+)'\)", style)
                        if m:
                            img_url = m.group(1)
                            try:
                                img_path = MEDIA_DIR / f"scrape_{idx}_{len(media_items)}.jpg"
                                async with httpx.AsyncClient(timeout=20) as cc:
                                    img = await cc.get(img_url)
                                    if img.status_code==200:
                                        img_path.write_bytes(img.content)
                                        media_items.append(MediaItem(idx, str(img_path), "image/jpeg"))
                            except Exception:
                                pass
                    # also inline photos
                    for img in wrap.select("img"):
                        src = img.get("src")
                        if src and "telegram" not in src:
                            try:
                                img_path = MEDIA_DIR / f"scrape_{idx}_{len(media_items)}.jpg"
                                async with httpx.AsyncClient(timeout=20) as cc:
                                    im = await cc.get(src)
                                    if im.status_code==200:
                                        img_path.write_bytes(im.content)
                                        media_items.append(MediaItem(idx, str(img_path), "image/jpeg"))
                            except Exception:
                                pass
                    posts.append(SupplierPost(idx, None, text, media_items))
                # return last 30, caller filters by days/parse
                return posts[-30:]
        except Exception as e:
            print(f"webscrape fallback error: {e}")
            return []

    async def collect_recent_posts(self, days: int = 14) -> list[SupplierPost]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        if _has_mtproto() and self.client:
            try:
                return await self._collect_mtproto(cutoff)
            except Exception as e:
                print(f"mtproto failed, fallback webscrape: {e}")
        return await self._collect_webscrape(days)

    async def extract_products(self, days: int = 14):
        posts = await self.collect_recent_posts(days)
        products = []
        for post in posts:
            parsed = parse_product(post.text)
            if not parsed:
                continue
            media = select_product_media(post)
            if not media:
                continue
            products.append((parsed, media, post))
        return products
