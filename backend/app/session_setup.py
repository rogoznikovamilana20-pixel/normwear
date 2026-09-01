import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from .config import settings

async def main():
    client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
    await client.start()
    print('\nSUPPLIER_SESSION_STRING=')
    print(client.session.save())
    await client.disconnect()

if __name__ == '__main__': asyncio.run(main())
