import httpx, json

r = httpx.post('https://normwear-api.onrender.com/api/products', json={
    'title': 'Test Product',
    'description': 'Test',
    'category': 'Test',
    'purchase_price': 1000,
    'sale_price': 1350,
    'sizes_json': ['S', 'M'],
    'stock': 5,
}, timeout=30)
print(f'Status: {r.status_code}')
print(f'Headers: {dict(r.headers)}')
print(f'Body: {r.text[:500]}')
