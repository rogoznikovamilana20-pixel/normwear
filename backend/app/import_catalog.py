"""
Import catalog via API
Run: python -m backend.app.import_catalog
"""
import json, sys, asyncio, httpx
from pathlib import Path

API_URL = "https://normwear-api.onrender.com"
CATALOG_PATH = Path(__file__).parent.parent.parent / "catalog_optobaza.json"
MARGIN = 1.35

async def main():
    if not CATALOG_PATH.exists():
        print(f"Catalog not found: {CATALOG_PATH}", file=sys.stderr)
        return

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    print(f"Loaded {len(catalog)} products", file=sys.stderr)

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API_URL}/api/products?limit=1000")
        existing = {p["title"] for p in r.json()} if r.status_code == 200 else set()
        print(f"Existing in DB: {len(existing)}", file=sys.stderr)

    imported = 0
    skipped = 0
    errors = 0

    async with httpx.AsyncClient(timeout=30) as c:
        for item in catalog:
            title = item["title"][:200]
            if title in existing:
                skipped += 1
                continue

            purchase_price = float(item["price"])
            sale_price = round(purchase_price * MARGIN)
            sizes = item.get("sizes", "—")
            if sizes == "—":
                sizes_json = []
            else:
                sizes_json = [s.strip() for s in sizes.split(",") if s.strip()]
            brand = item.get("brand", "Other")
            category = item.get("category", "Other")
            description = item.get("description", "")[:500]
            stock = min(item.get("stock", 1), 10)

            payload = {
                "title": title,
                "description": f"{brand} | {description}" if description else brand,
                "category": category,
                "purchase_price": purchase_price,
                "sale_price": sale_price,
                "sizes_json": sizes_json,
                "stock": stock,
            }

            for attempt in range(3):
                try:
                    r = await c.post(f"{API_URL}/api/products", json=payload)
                    if r.status_code == 200:
                        imported += 1
                        existing.add(title)
                        break
                    elif r.status_code == 429:
                        await asyncio.sleep(5)
                    elif r.status_code == 409:
                        skipped += 1
                        existing.add(title)
                        break
                    else:
                        errors += 1
                        if errors <= 5:
                            print(f"Error {r.status_code}: {r.text[:100]}", file=sys.stderr)
                        break
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print(f"Exception: {e}", file=sys.stderr)
                    break

            await asyncio.sleep(0.3)
            if imported % 50 == 0 and imported > 0:
                print(f"  ...imported {imported}", file=sys.stderr)

    print(f"\n=== DONE ===", file=sys.stderr)
    print(f"Imported: {imported}", file=sys.stderr)
    print(f"Skipped: {skipped}", file=sys.stderr)
    print(f"Errors: {errors}", file=sys.stderr)

asyncio.run(main())
