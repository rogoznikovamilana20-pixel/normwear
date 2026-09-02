"""Import missing Excel products to DB, then enrich with images"""
import json, sys, asyncio, httpx, re
from pathlib import Path

API_URL = "https://normwear-api.onrender.com"
EXCEL_JSON = Path(r'C:\Users\andre\Downloads\normwear-shop-v0.4\normwear-shop\backend\moysklad_excel_products.json')
DEFAULT_MARGIN = 1.35

async def main():
    with open(EXCEL_JSON, 'r', encoding='utf-8') as f:
        excel = json.load(f)
    
    # Fetch all DB products and build code set
    all_db = []
    async with httpx.AsyncClient(timeout=120) as c:
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
    
    db_codes = set()
    for p in all_db:
        m = re.search(r'\[(\d+)\]$', p.get('title', ''))
        if m:
            db_codes.add(m.group(1))
    
    print(f"DB products: {len(all_db)}, codes: {len(db_codes)}", file=sys.stderr)
    
    # Filter missing
    missing = [ep for ep in excel if ep.get('code', '') not in db_codes]
    print(f"Missing from DB: {len(missing)}", file=sys.stderr)
    
    # Build bulk-create payloads (for bulk endpoint)
    creates = []
    for ep in missing:
        code = ep.get('code', '')
        name = ep.get('name', '')
        if not name or name == 'ИЗ':
            continue
        
        retail = ep.get('retail_price', 0)
        wholesale = ep.get('wholesale_price', 0)
        purchase = ep.get('purchase_price', 0)
        
        if wholesale > 0:
            sale = round(wholesale * DEFAULT_MARGIN)
            purchase_price = wholesale
        elif purchase > 0:
            sale = round(purchase * DEFAULT_MARGIN)
            purchase_price = purchase
        else:
            sale = 1500
            purchase_price = 0
        
        sizes = ep.get('sizes', [])
        if not sizes:
            sizes = ["XS", "S", "M", "L", "XL", "XXL"]
        
        title = ep.get('title', f"{name} [{code}]")
        
        creates.append({
            'title': title,
            'description': ep.get('description', ''),
            'category': ep.get('category', ''),
            'purchase_price': purchase_price,
            'sale_price': sale,
            'sizes_json': sizes,
            'stock': 1,
        })
    
    print(f"Products to create: {len(creates)}", file=sys.stderr)
    
    # Create in batches
    total_created = 0
    total_skipped = 0
    BATCH = 50
    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(0, len(creates), BATCH):
            batch = creates[i:i+BATCH]
            for attempt in range(5):
                try:
                    r = await c.post(f"{API_URL}/api/products/bulk", json={"products": batch})
                    if r.status_code == 200:
                        data = r.json()
                        total_created += data.get('created', 0)
                        total_skipped += data.get('skipped', 0)
                        print(f"  Batch {i//BATCH+1}: +{data.get('created',0)} created, {data.get('skipped',0)} skipped (total: {total_created})", file=sys.stderr)
                        break
                    elif r.status_code == 429:
                        await asyncio.sleep(10)
                    else:
                        print(f"  Error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        break
                except Exception as e:
                    print(f"  Exception: {str(e)[:100]}", file=sys.stderr)
                    await asyncio.sleep(5)
            await asyncio.sleep(1)
    
    print(f"\nCreated: {total_created}, Skipped: {total_skipped}", file=sys.stderr)
    
    # Now enrich ALL products (both old and new) with images
    print(f"\n=== ENRICHING WITH IMAGES ===", file=sys.stderr)
    
    # Re-fetch all DB products
    all_db2 = []
    async with httpx.AsyncClient(timeout=120) as c:
        offset = 0
        while True:
            r = await c.get(f"{API_URL}/api/products?limit=100&offset={offset}")
            data = r.json()
            if not data:
                break
            all_db2.extend(data)
            offset += 100
            if len(data) < 100:
                break
    
    print(f"DB products after create: {len(all_db2)}", file=sys.stderr)
    
    # Build code→excel lookup
    excel_by_code = {ep['code']: ep for ep in excel if ep.get('code')}
    
    # Build updates
    updates = []
    for p in all_db2:
        m = re.search(r'\[(\d+)\]$', p.get('title', ''))
        if not m:
            continue
        code = m.group(1)
        ep = excel_by_code.get(code)
        if not ep:
            continue
        
        # Build media
        media = []
        img = ep.get('image_url', '')
        if img:
            for url in img.split(';'):
                url = url.strip()
                if url.startswith('http'):
                    media.append(url)
        
        sizes = ep.get('sizes', [])
        if not sizes:
            sizes = ["XS", "S", "M", "L", "XL", "XXL"]
        
        wholesale = ep.get('wholesale_price', 0)
        purchase = wholesale if wholesale > 0 else ep.get('purchase_price', 0)
        if wholesale > 0:
            sale = round(wholesale * DEFAULT_MARGIN)
        elif purchase > 0:
            sale = round(purchase * DEFAULT_MARGIN)
        else:
            sale = 1500
        
        desc = ep.get('description', '')
        category = ep.get('category', '')
        brand = ep.get('brand', '')
        
        update = {'title': p['title']}
        if media:
            update['media_json'] = media
        update['sizes_json'] = sizes
        if desc:
            update['description'] = desc
        if category:
            update['category'] = category
        if brand:
            update['brand'] = brand
        if purchase > 0:
            update['purchase_price'] = purchase
        if sale > 0:
            update['sale_price'] = sale
        
        updates.append(update)
    
    print(f"Updates to apply: {len(updates)}", file=sys.stderr)
    
    # Apply in batches
    total_updated = 0
    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(0, len(updates), BATCH):
            batch = updates[i:i+BATCH]
            for attempt in range(5):
                try:
                    r = await c.post(f"{API_URL}/api/products/bulk-update", json={"updates": batch})
                    if r.status_code == 200:
                        data = r.json()
                        total_updated += data.get('updated', 0)
                        print(f"  Enrich {i//BATCH+1}: +{data.get('updated',0)} (total: {total_updated})", file=sys.stderr)
                        break
                    elif r.status_code == 429:
                        await asyncio.sleep(10)
                    else:
                        print(f"  Error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        break
                except Exception as e:
                    print(f"  Exception: {str(e)[:100]}", file=sys.stderr)
                    await asyncio.sleep(5)
            await asyncio.sleep(1)
    
    print(f"\n=== DONE ===", file=sys.stderr)
    print(f"Created: {total_created}", file=sys.stderr)
    print(f"Enriched: {total_updated}", file=sys.stderr)

asyncio.run(main())
