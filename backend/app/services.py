from dataclasses import dataclass

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
