from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from .config import settings, get_shop_bot
from .ai_copy import manual_post
import json, tempfile, os, contextlib

class ChannelPublisher:
    def __init__(self):
        self.bot = get_shop_bot()

    async def _download(self, url: str) -> str | None:
        try:
            session = self.bot.session
            resp = await session.get(url)
            if resp.status == 200:
                data = await resp.read()
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                tmp.write(data)
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
            if u.startswith('http'):
                path = await self._download(u)
                if path:
                    files.append(path)
            else:
                files.append(u)
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
