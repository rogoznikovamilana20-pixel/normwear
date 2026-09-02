"""
Enrich DB products with Excel data: images, sizes, colors, prices, descriptions.
Matches by code in title: "Oakley [00201]" ↔ code "00201"
"""
import json, sys, asyncio, httpx, re
from pathlib import Path

API_URL = "https://normwear-api.onrender.com"
EXCEL_JSON = Path(__file__).parent.parent / "moysklad_excel_products.json"
MARGIN = 2.0  # retail = wholesale * 2

async def main():
    if not EXCEL_JSON.exists():
        print(f"Not found: {EXCEL_JSON}", file=sys.stderr)
        return

    with open(EXCEL_JSON, 'r', encoding='utf-8') as f:
        excel_products = json.load(f)

    print(f"Excel products: {len(excel_products)}", file=sys.stderr)

    # Fetch current DB products
    async with httpx.AsyncClient(timeout=120) as c:
        all_db = []
        offset = 0
        while True:
            r = await c.get(f"{API_URL}/api/products?limit=100&offset={offset}")
            data = r.json()
            if not data:
                break
            all_db.extend(data)
            offset += 100
            if len(data) < 100:
                break

    print(f"DB products: {len(all_db)}", file=sys.stderr)

    # Build lookup: extract code from title "Name [CODE]" → code
    db_by_code = {}
    for p in all_db:
        title = p.get('title', '')
        m = re.search(r'\[(\d+)\]$', title)
        if m:
            code = m.group(1)
            db_by_code[code] = p

    print(f"DB products with code: {len(db_by_code)}", file=sys.stderr)

    # Build updates
    updates = []
    matched = 0
    for ep in excel_products:
        code = ep.get('code', '')
        if not code:
            continue
        db_product = db_by_code.get(code)
        if not db_product:
            continue
        matched += 1

        # Build media_json: list of image URLs
        media = []
        if ep.get('image_url'):
            media.append(ep['image_url'])

        # Sizes from Excel variants
        sizes = ep.get('sizes', [])
        if not sizes:
            sizes = ["XS", "S", "M", "L", "XL", "XXL"]

        # Description from Excel
        desc = ep.get('description', '')

        # Category
        category = ep.get('category', '')

        # Prices
        purchase = ep.get('purchase_price', 0)
        retail = ep.get('retail_price', 0)
        wholesale = ep.get('wholesale_price', 0)

        # Use wholesale as purchase price (opit), retail as sale price
        if wholesale > 0:
            purchase = wholesale
        if retail > 0:
            sale = retail
        elif purchase > 0:
            sale = round(purchase * MARGIN)
        else:
            sale = 1500  # minimum

        # Brand
        brand = ep.get('brand', '')

        update = {
            'title': db_product['title'],
        }
        if media:
            update['media_json'] = media
        update['sizes_json'] = sizes
        update['description'] = desc
        update['category'] = category
        update['brand'] = brand
        if purchase > 0:
            update['purchase_price'] = purchase
        if sale > 0:
            update['sale_price'] = sale

        updates.append(update)

    print(f"Updates to apply: {len(updates)} (matched: {matched})", file=sys.stderr)

    # Send in batches of 50
    total_updated = 0
    total_not_found = 0
    BATCH = 50

    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(0, len(updates), BATCH):
            batch = updates[i:i+BATCH]
            for attempt in range(5):
                try:
                    r = await c.post(f"{API_URL}/api/products/bulk-update", json={"updates": batch})
                    if r.status_code == 200:
                        data = r.json()
                        total_updated += data.get('updated', 0)
                        total_not_found += data.get('not_found', 0)
                        print(f"  Batch {i//BATCH+1}: +{data.get('updated',0)} updated (total: {total_updated})", file=sys.stderr)
                        break
                    elif r.status_code == 429:
                        print(f"  Rate limit, waiting 10s...", file=sys.stderr)
                        await asyncio.sleep(10)
                    else:
                        print(f"  Error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        break
                except Exception as e:
                    print(f"  Exception: {str(e)[:100]}", file=sys.stderr)
                    await asyncio.sleep(5)
            await asyncio.sleep(2)

    print(f"\n=== DONE ===", file=sys.stderr)
    print(f"Updated: {total_updated}", file=sys.stderr)
    print(f"Not found: {total_not_found}", file=sys.stderr)

asyncio.run(main())
