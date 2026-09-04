from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from .config import settings, get_shop_bot
from .ai_copy import manual_post
import json, tempfile, os, contextlib, httpx

YANDEX_DISK_PUBLIC_KEY = "https://disk.yandex.ru/d/e4YGRLhebhBoVA"
YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"

class ChannelPublisher:
    def __init__(self):
        self.bot = get_shop_bot()

    async def _resolve_yandex_url(self, stored: str) -> str | None:
        if stored.startswith('http') and 'downloader.disk.yandex.ru' not in stored and 'cloud-api.yandex.net' not in stored:
            return stored
        path = stored
        if not path.startswith('/'):
            path = '/' + stored
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(YANDEX_DISK_API, params={"public_key": YANDEX_DISK_PUBLIC_KEY, "path": path})
                if r.status_code == 200:
                    data = r.json()
                    href = data.get('href', '')
                    if href:
                        return href
        except Exception as e:
            print(f'[publisher] yandex resolve error {path}: {e}', flush=True)
        if stored.startswith('http'):
            return stored
        return None

    async def _download(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    ct = resp.headers.get('content-type', '')
                    suffix = '.jpg'
                    if 'png' in ct:
                        suffix = '.png'
                    elif 'webp' in ct:
                        suffix = '.webp'
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    tmp.write(resp.content)
                    tmp.close()
                    return tmp.name
        except Exception as e:
            print(f'[publisher] download error: {e}', flush=True)
        return None

    def _cleanup(self, files: list[str]):
        for f in files:
            with contextlib.suppress(Exception):
                os.unlink(f)

    async def publish(self, product, media_paths: list[str]):
        sizes = json.loads(product.sizes_json)
        caption = manual_post(
            title=product.title,
            brand=product.brand or product.category or '',
            description=product.description or '',
            price=float(product.sale_price),
            sizes=sizes,
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🛍 Купить в каталоге', url=f'https://t.me/{settings.shop_username}')],
            [InlineKeyboardButton(text='💬 Спросить в боте', url=f'https://t.me/{settings.shop_username}')],
        ])
        files = []
        for u in media_paths[:6]:
            resolved = await self._resolve_yandex_url(u)
            if resolved:
                path = await self._download(resolved)
                if path:
                    files.append(path)
        if not files:
            msg = await self.bot.send_message(settings.shop_channel_id, caption, parse_mode='HTML', reply_markup=markup)
            return msg.message_id
        try:
            if len(files) == 1:
                msg = await self.bot.send_photo(
                    settings.shop_channel_id, FSInputFile(files[0]),
                    caption=caption, parse_mode='HTML', reply_markup=markup,
                )
                return msg.message_id
            group = [
                InputMediaPhoto(
                    media=FSInputFile(f),
                    parse_mode='HTML' if i == 0 else None,
                    caption=caption if i == 0 else None,
                )
                for i, f in enumerate(files)
            ]
            await self.bot.send_media_group(settings.shop_channel_id, group)
            control = await self.bot.send_message(
                settings.shop_channel_id,
                '🛍 Новый товар в каталоге:',
                reply_markup=markup,
            )
            return control.message_id
        finally:
            self._cleanup(files)
