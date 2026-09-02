"""
Parse МойСклад Excel export → clean JSON for import.
Produces BOTH parent products AND individual variant products (one per code).
"""
import openpyxl, json
from pathlib import Path
from collections import defaultdict

INPUT = Path(r'C:\Users\andre\Downloads\Telegram Desktop\20260902150228_admin@bazhukovsergey_665440570.xlsx')
OUTPUT = Path(__file__).parent.parent / 'moysklad_excel_products.json'

wb = openpyxl.load_workbook(INPUT, data_only=True)
ws = wb['Sheet0']

def parse_price(val):
    if val is None:
        return 0.0
    s = str(val).replace(',', '.').replace(' ', '').replace('\xa0', '')
    try:
        return float(s)
    except ValueError:
        return 0.0

# Pass 1: collect all Товар as parents, then attach Модификации
products = {}  # code → product dict
current_product = None

for r in range(2, ws.max_row + 1):
    row_type = ws.cell(r, 3).value
    code = str(ws.cell(r, 4).value or '').strip()
    name = ws.cell(r, 5).value
    image = ws.cell(r, 55).value
    retail = ws.cell(r, 9).value
    wholesale = ws.cell(r, 11).value
    purchase = ws.cell(r, 17).value
    color = ws.cell(r, 56).value
    size = ws.cell(r, 57).value
    uuid = ws.cell(r, 2).value
    group = ws.cell(r, 1).value
    parent_code = ws.cell(r, 37).value  # for modifications

    if row_type == 'Товар':
        category = ''
        brand = ''
        if group:
            parts = group.split('/')
            category = parts[0].strip()
            if len(parts) > 1:
                brand = parts[1].strip()

        current_product = {
            'code': code,
            'uuid': str(uuid) if uuid else '',
            'name': str(name).strip() if name else '',
            'category': category,
            'brand': brand,
            'retail_price': parse_price(retail),
            'wholesale_price': parse_price(wholesale),
            'purchase_price': parse_price(purchase),
            'image_url': str(image).strip() if image and str(image).startswith('http') else '',
            'sizes': [],
            'colors': [],
            'mods': [],
        }
        products[code] = current_product

    elif row_type == 'Модификация' and current_product:
        mod = {
            'code': code,
            'uuid': str(uuid) if uuid else '',
            'color': str(color).strip() if color else '',
            'size': str(size).strip() if size else '',
            'retail_price': parse_price(ws.cell(r, 9).value),
            'purchase_price': parse_price(ws.cell(r, 17).value),
            'image_url': str(image).strip() if image and str(image).startswith('http') else '',
        }
        current_product['mods'].append(mod)
        if mod['size'] and mod['size'] not in current_product['sizes']:
            current_product['sizes'].append(mod['size'])
        if mod['color'] and mod['color'] not in current_product['colors']:
            current_product['colors'].append(mod['color'])

wb.close()

# Pass 2: build flat list with BOTH parent-level and variant-level entries
result = []
size_order = {'XXS': 0, 'XS': 1, 'S': 2, 'M': 3, 'L': 4, 'XL': 5, '2XL': 6, '3XL': 7, '4XL': 8}

for code, p in products.items():
    if not p['name'] or p['name'] == 'ИЗ':
        continue

    p['sizes'].sort(key=lambda s: size_order.get(s.upper(), 99))
    desc_parts = []
    if p['brand']:
        desc_parts.append(p['brand'])
    if p['colors']:
        desc_parts.append(f"Цвета: {', '.join(p['colors'])}")
    description = ' | '.join(desc_parts) if desc_parts else p['brand']

    # Determine retail price
    retail = p['retail_price']
    if retail <= 0 and p['mods']:
        for m in p['mods']:
            if m['retail_price'] > 0:
                retail = m['retail_price']
                break

    # Parent-level entry
    parent_title = f"{p['name']} [{p['code']}]" if p['code'] else p['name']
    entry = {
        'code': p['code'],
        'uuid': p['uuid'],
        'title': parent_title,
        'name': p['name'],
        'category': p['category'],
        'brand': p['brand'],
        'description': description,
        'retail_price': retail,
        'wholesale_price': p['wholesale_price'],
        'purchase_price': p['purchase_price'],
        'image_url': p['image_url'],
        'sizes': p['sizes'],
        'colors': p['colors'],
        'mod_count': len(p['mods']),
        'mods': p['mods'],
    }
    result.append(entry)

    # Variant-level entries: one per modification code
    for m in p['mods']:
        mod_title = f"{p['name']} ({m['size']}, {m['color']}) [{m['code']}]" if m['code'] else p['name']
        mod_retail = m['retail_price'] if m['retail_price'] > 0 else retail
        mod_entry = {
            'code': m['code'],
            'uuid': m['uuid'],
            'title': mod_title,
            'name': p['name'],
            'category': p['category'],
            'brand': p['brand'],
            'description': description,
            'retail_price': mod_retail,
            'wholesale_price': p['wholesale_price'],
            'purchase_price': m['purchase_price'] if m['purchase_price'] > 0 else p['purchase_price'],
            'image_url': m.get('image_url') or p['image_url'],
            'sizes': [m['size']] if m['size'] else [],
            'colors': [m['color']] if m['color'] else [],
            'mod_count': 0,
            'mods': [],
        }
        result.append(mod_entry)

result.sort(key=lambda x: (x['category'], x['brand'], x['name'], x['code']))

with_image = sum(1 for p in result if p['image_url'])
with_price = sum(1 for p in result if p['retail_price'] > 0)
print(f"Total entries: {len(result)}")
print(f"Parents: {sum(1 for p in result if p['mod_count'] > 0)}")
print(f"Variants: {sum(1 for p in result if p['mod_count'] == 0)}")
print(f"With image: {with_image}")
print(f"With price > 0: {with_price}")

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Saved: {OUTPUT}")
