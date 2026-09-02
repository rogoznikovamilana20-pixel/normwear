from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, InputMediaPhoto
from .config import settings, get_shop_bot
from .ai_copy import manual_post

class ChannelPublisher:
    def __init__(self):
        self.bot = get_shop_bot()

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
        usable = [p for p in media_paths if p]
        if len(usable) == 1:
            msg = await self.bot.send_photo(settings.shop_channel_id, FSInputFile(usable[0]), caption=caption, parse_mode='HTML', reply_markup=markup)
            return msg.message_id
        if usable:
            group = [InputMediaPhoto(media=FSInputFile(p), parse_mode='HTML' if i == 0 else None, caption=caption if i == 0 else None) for i, p in enumerate(usable[:10])]
            sent = await self.bot.send_media_group(settings.shop_channel_id, group)
            control = await self.bot.send_message(settings.shop_channel_id, '🛍 Новый товар в каталоге:', reply_markup=markup)
            return control.message_id
        msg = await self.bot.send_message(settings.shop_channel_id, caption, parse_mode='HTML', reply_markup=markup)
        return msg.message_id
