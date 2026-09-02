"""
Fix media_json: split semicolons into proper arrays.
"""
import json, sys, asyncio, httpx, re

API_URL = "https://normwear-api.onrender.com"

async def main():
    # Fetch ALL products
    all_products = []
    offset = 0
    async with httpx.AsyncClient(timeout=120) as c:
        while True:
            r = await c.get(f"{API_URL}/api/products?limit=100&offset={offset}")
            data = r.json()
            if not data:
                break
            all_products.extend(data)
            offset += 100
            if len(data) < 100:
                break

    print(f"Total products: {len(all_products)}", file=sys.stderr)

    # Find products with semicolons in media
    updates = []
    for p in all_products:
        media = p.get('media', [])
        fixed = []
        needs_fix = False
        for m in media:
            if ';' in m:
                needs_fix = True
                for part in m.split(';'):
                    part = part.strip()
                    if part.startswith('http'):
                        fixed.append(part)
                    elif part:
                        fixed.append(part)
            else:
                fixed.append(m)
        if needs_fix:
            updates.append({'title': p['title'], 'media_json': fixed})

    print(f"Products needing fix: {len(updates)}", file=sys.stderr)

    # Fix in batches
    total_fixed = 0
    BATCH = 50
    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(0, len(updates), BATCH):
            batch = updates[i:i+BATCH]
            for attempt in range(5):
                try:
                    r = await c.post(f"{API_URL}/api/products/bulk-update", json={"updates": batch})
                    if r.status_code == 200:
                        data = r.json()
                        total_fixed += data.get('updated', 0)
                        print(f"  Batch {i//BATCH+1}: +{data.get('updated',0)} fixed (total: {total_fixed})", file=sys.stderr)
                        break
                    elif r.status_code == 429:
                        await asyncio.sleep(10)
                    else:
                        print(f"  Error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        break
                except Exception as e:
                    print(f"  Exception: {str(e)[:100]}", file=sys.stderr)
                    await asyncio.sleep(5)
            await asyncio.sleep(2)

    print(f"\nFixed: {total_fixed}", file=sys.stderr)

asyncio.run(main())
