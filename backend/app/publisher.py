from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, InputMediaPhoto
from .config import settings, get_shop_bot
from .ai_copy import manual_post
import aiohttp, tempfile, os

class ChannelPublisher:
    def __init__(self):
        self.bot = get_shop_bot()

    async def _download(self, url: str) -> str | None:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
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
        import json
        sizes = json.loads(product.sizes_json)
        caption = manual_post(
            title=product.title,
            brand=product.brand or product.category or '',
            description=product.description or '',
            price=float(product.sale_price),
            sizes=sizes,
        )
        webapp_url = settings.miniapp_url_template.format(product_id=product.id)
        bot_username = 'norm_shop_bot'
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🛍 Купить', web_app=WebAppInfo(url=webapp_url))],
            [InlineKeyboardButton(text='💬 Спросить в боте', url=f'https://t.me/{bot_username}')],
        ])
        # download all images
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
            control = await self.bot.send_message(settings.shop_channel_id, '🛍 Новый товар в каталоге:', reply_markup=markup)
            return control.message_id
        finally:
            for f in files:
                try: os.unlink(f)
                except: pass
