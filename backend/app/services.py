from dataclasses import dataclass
from typing import Iterable

@dataclass
class MediaItem:
    source_message_id: int
    file_path: str
    mime_type: str | None

@dataclass
class SupplierPost:
    message_id: int
    grouped_id: int | None
    text: str
    media: list[MediaItem]


def select_product_media(post: SupplierPost) -> list[MediaItem]:
    # IMPORTANT: never drop media based on its position.
    # The supplier channel avatar is NOT part of a post's media and must not
    # be treated as the first product image. Every image attached to the
    # supplier post/album is therefore preserved.
    return [m for m in post.media if m.mime_type is None or m.mime_type.startswith('image/')]


def build_channel_caption(title: str, description: str, price: float, sizes: Iterable[str]) -> str:
    size_line = ", ".join(sizes) if sizes else "уточняйте наличие"
    clean = description.replace("<", "&lt;").replace(">", "&gt;")[:700]
    return (
        f"🔥 <b>{title}</b>\n\n"
        f"{clean}\n\n"
        f"Размеры: <b>{size_line}</b>\n"
        f"Цена: <b>{price:,.0f} ₽</b>\n\n"
        "📦 В наличии\n"
        "🛍 <b>Заказать — кнопка ниже</b>"
    )
