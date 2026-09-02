from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from .config import settings, get_shop_bot
from .ai_copy import manual_post
import json, tempfile, os

class ChannelPublisher:
    def __init__(self):
        self.bot = get_shop_bot()

    async def _download(self, url: str) -> str | None:
        """Download image via bot's aiohttp session"""
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
            print(f'download error: {e}', flush=True)
        return None

    async def publish(self, product, media_paths: list[str]):
        sizes = json.loads(product.sizes_json)
        caption = manual_post(
            title=product.title,
            brand=product.brand or product.category or '',
            description=product.description or '',
            price=float(product.sale_price),
            sizes=sizes,
        )
        bot_username = 'norm_shop_bot'
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='\U0001f6cd\ufe0f \u041a\u0443\u043f\u0438\u0442\u044c \u0432 \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0435', url=f'https://t.me/{bot_username}')],
            [InlineKeyboardButton(text='\U0001f4ac \u0421\u043f\u0440\u043e\u0441\u0438\u0442\u044c \u0432 \u0431\u043e\u0442\u0435', url=f'https://t.me/{bot_username}')],
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
                msg = await self.bot.send_photo(settings.shop_channel_id, FSInputFile(files[0]), caption=caption, parse_mode='HTML', reply_markup=markup)
                return msg.message_id
            group = [InputMediaPhoto(media=FSInputFile(f), parse_mode='HTML' if i == 0 else None, caption=caption if i == 0 else None) for i, f in enumerate(files)]
            sent = await self.bot.send_media_group(settings.shop_channel_id, group)
            control = await self.bot.send_message(settings.shop_channel_id, '\U0001f6cd\ufe0f \u041d\u043e\u0432\u044b\u0439 \u0442\u043e\u0432\u0430\u0440 \u0432 \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0435:', reply_markup=markup)
            return control.message_id
        finally:
            for f in files:
                try: os.unlink(f)
                except: pass
