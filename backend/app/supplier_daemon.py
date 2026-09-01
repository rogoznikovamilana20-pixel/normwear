import asyncio
from .pipeline import ingest_supplier

async def main():
    # Initial 14-day backfill, then continuously poll for new/changed posts.
    first = True
    while True:
        try:
            result = await ingest_supplier(days=7 if first else 1)
            print(result, flush=True)
            first = False
        except Exception as exc:
            print(f"supplier sync error: {exc!r}", flush=True)
        await asyncio.sleep(60)

if __name__ == '__main__': asyncio.run(main())
