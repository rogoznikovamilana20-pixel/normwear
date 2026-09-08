import asyncio
import logging

log = logging.getLogger("supplier_watcher")


def is_configured(settings) -> bool:
    return bool(
        settings.telegram_api_id
        and settings.telegram_api_hash
        and settings.supplier_session_string
        and settings.supplier_channel_username
    )


async def run_watcher(settings, on_post) -> None:
    """Фаза 9: слушает канал поставщика через Telethon и отдаёт новые посты в on_post(chat_id, text, file_ids).

    Фото скачиваются локально в data/supplier/, т.к. file_id юзербота боту не подойдёт.
    """
    from pathlib import Path

    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    from app.config import BASE_DIR

    media_dir = BASE_DIR / "data" / "supplier"
    media_dir.mkdir(parents=True, exist_ok=True)

    proxy = None
    if settings.telegram_proxy_url:
        # формат socks5://user:pass@host:port
        from urllib.parse import urlparse

        u = urlparse(settings.telegram_proxy_url)
        proxy = ("socks5", u.hostname, u.port or 1080, True, u.username, u.password)

    client = TelegramClient(
        StringSession(settings.supplier_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=proxy,
    )
    await client.start()
    entity = await client.get_entity(settings.supplier_channel_username)
    log.info("Watcher слушает @%s", settings.supplier_channel_username)

    # сначала догоняем последние 10 постов (черновики)
    try:
        async for msg in client.iter_messages(entity, limit=10):
            await _handle(client, media_dir, msg, on_post)
    except Exception as e:
        log.warning("catch-up failed: %s", e)

    @client.on(events.NewMessage(chats=entity))
    async def _new(event):
        await _handle(client, media_dir, event.message, on_post)

    await client.run_until_disconnected()


async def _handle(client, media_dir, msg, on_post) -> None:
    try:
        text = msg.text or ""
        if not text.strip():
            return
        from app.services.photos import store_local

        local_photos: list[str] = []
        if msg.photo:
            try:
                path = await client.download_media(msg, file=str(media_dir / f"{msg.id}.jpg"))
                if path:
                    from pathlib import Path as _P

                    local_photos.append(store_local(_P(path).name))
            except Exception:
                pass
        await on_post(text, local_photos)
    except Exception:
        pass


async def run_forever(settings, on_post) -> None:
    while True:
        try:
            await run_watcher(settings, on_post)
        except Exception as e:
            log.warning("watcher упал, рестарт через 60с: %s", e)
        await asyncio.sleep(60)
