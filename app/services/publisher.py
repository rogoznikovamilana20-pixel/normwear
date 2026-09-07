import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto

from app.config import Settings
from app.models import Brand, ChannelPost, Product, ProductPhoto
from app.services.photos import YandexLibrary


def display_name(product: Product, brand_title: str | None) -> str:
    if brand_title and product.model and brand_title.lower() not in product.title.lower():
        return f"{brand_title} {product.model}"
    return product.title


def build_caption(product: Product, brand_title: str | None, settings: Settings) -> str:
    sizes = ", ".join((product.sizes or [])[:14]) or "—"
    name = html.escape(display_name(product, brand_title))
    return (
        f"🔥 <b>{name}</b>\n"
        f"\n"
        f"📏 Размеры: {html.escape(sizes)}\n"
        f"💰 Цена: <b>{int(product.retail_price or 0)} ₽</b>\n"
        f"🚚 Доставка: 2–4 дня\n"
        f"\n"
        f"🛒 Заказ: @{settings.shop_username}"
    )


async def collect_media(session: AsyncSession, product: Product, library: YandexLibrary) -> list[InputMediaPhoto]:
    rows = (await session.scalars(
        select(ProductPhoto).where(ProductPhoto.product_id == product.id).order_by(ProductPhoto.position)
    )).all()
    import os

    supplier = [r.url for r in rows if r.source == "supplier"]
    yandex = [r.url for r in rows if r.source == "yandex"]
    local = [r.url for r in rows if r.source == "local" and isinstance(r.url, str) and os.path.exists(r.url)]
    chosen: list[str] = []
    local_chosen: list[str] = []
    if product.photo_mode == "yandex":
        for path in yandex[:10]:
            try:
                href = await library.download_url(path)
                if href:
                    chosen.append(href)
            except Exception:
                continue
        local_chosen.extend(local[: max(0, 10 - len(chosen))])
        chosen.extend(supplier[: max(0, 10 - len(chosen) - len(local_chosen))])
    else:
        chosen.extend(supplier[:10])
        local_chosen.extend(local[: max(0, 10 - len(chosen))])
        for path in yandex[: max(0, 10 - len(chosen) - len(local_chosen))]:
            try:
                href = await library.download_url(path)
                if href:
                    chosen.append(href)
            except Exception:
                continue
    media = [InputMediaPhoto(media=u) for u in chosen]
    media += [InputMediaPhoto(media=FSInputFile(p)) for p in local_chosen]
    return media


async def publish_product(bot: Bot, session: AsyncSession, product: Product, library: YandexLibrary, settings: Settings) -> int | None:
    if not settings.shop_channel_id:
        raise ValueError("SHOP_CHANNEL_ID не задан — проверь .env")
    brand_title = None
    if product.brand_id:
        brand = await session.get(Brand, product.brand_id)
        brand_title = brand.title if brand else None
    caption = build_caption(product, brand_title, settings)
    media = await collect_media(session, product, library)
    if media:
        media[0].caption = caption
        media[0].parse_mode = "HTML"
        msgs = await bot.send_media_group(chat_id=settings.shop_channel_id, media=media)
        message_id = msgs[0].message_id
    else:
        msg = await bot.send_message(chat_id=settings.shop_channel_id, text=caption)
        message_id = msg.message_id
    session.add(ChannelPost(product_id=product.id, message_id=message_id, channel_id=str(settings.shop_channel_id), text=caption))
    # кнопка воронки под постом — медиагруппа не держит кнопки, шлём отдельным сообщением
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

        miniapp_base = (settings.miniapp_url_template or "").split("?")[0].replace("/app/", "/").rstrip("/") + "/"
        if not miniapp_base.startswith("https://"):
            miniapp_base = "https://normwear-shop.onrender.com/"
        kb_rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="✨ Открыть каталог", web_app=WebAppInfo(url=miniapp_base))],
            [InlineKeyboardButton(text="🛍 Открыть в боте", url=f"https://t.me/{settings.shop_username}?start=product_{product.id}")],
            [InlineKeyboardButton(text="🎡 Колесо фортуны", url=f"https://t.me/{settings.shop_username}?start=wheel")],
        ]
        await bot.send_message(chat_id=settings.shop_channel_id, text=f"👆 {display_name(product, brand_title)} — {int(product.retail_price or 0)}₽", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    except Exception:
        pass
    return message_id