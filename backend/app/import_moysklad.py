"""
Import МойСклад catalog via bulk API (100 at a time)
"""
import json, sys, asyncio, httpx
from pathlib import Path

API_URL = "https://normwear-api.onrender.com"
CATALOG_PATH = Path(__file__).parent.parent.parent / "moysklad_catalog.json"
MARGIN = 2.0

async def main():
    if not CATALOG_PATH.exists():
        print(f"Catalog not found: {CATALOG_PATH}", file=sys.stderr)
        return

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    print(f"Loaded {len(catalog)} products from catalog", file=sys.stderr)

    # Build payloads
    products = []
    for item in catalog:
        name = item.get("name", "").strip()
        if not name or len(name) < 2:
            continue
        
        code = item.get("code", "")
        title = f"{name} [{code}]" if code else name
        title = title[:200]

        purchase_price = float(item.get("price", 0))
        if purchase_price <= 0:
            purchase_price = 1300
        sale_price = round(purchase_price * MARGIN)
        if sale_price < 1500:
            sale_price = 1500

        category = item.get("category", "")
        brand = ""
        if "/" in category:
            parts = category.split("/")
            brand = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            category = parts[0].strip()
        
        description = item.get("description", "") or brand
        stock = int(item.get("stock", 1))
        if stock <= 0:
            stock = 1

        products.append({
            "title": title,
            "description": description,
            "category": category,
            "purchase_price": purchase_price,
            "sale_price": sale_price,
            "sizes_json": ["XS", "S", "M", "L", "XL", "XXL"],
            "stock": stock,
        })

    print(f"Prepared {len(products)} products for import", file=sys.stderr)

    total_created = 0
    total_skipped = 0
    BATCH = 50  # smaller batches for reliability

    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(0, len(products), BATCH):
            batch = products[i:i+BATCH]
            for attempt in range(5):
                try:
                    r = await c.post(f"{API_URL}/api/products/bulk", json={"products": batch})
                    if r.status_code == 200:
                        data = r.json()
                        created = data.get("created", 0)
                        skipped = data.get("skipped", 0)
                        total_created += created
                        total_skipped += skipped
                        print(f"  Batch {i//BATCH+1}: +{created} created, {skipped} skipped (total: {total_created})", file=sys.stderr)
                        break
                    elif r.status_code == 429:
                        print(f"  Rate limit at batch {i//BATCH+1}, waiting 10s...", file=sys.stderr)
                        await asyncio.sleep(10)
                    else:
                        print(f"  Error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        break
                except Exception as e:
                    print(f"  Exception (attempt {attempt+1}): {str(e)[:100]}", file=sys.stderr)
                    await asyncio.sleep(5)
            
            await asyncio.sleep(2)  # pause between batches

    print(f"\n=== DONE ===", file=sys.stderr)
    print(f"Created: {total_created}", file=sys.stderr)
    print(f"Skipped: {total_skipped}", file=sys.stderr)

asyncio.run(main())
