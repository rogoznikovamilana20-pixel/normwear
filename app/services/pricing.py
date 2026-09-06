def retail_price(supplier_price: float, margin_pct: float) -> int:
    v = supplier_price * (100.0 + margin_pct) / 100.0
    return int(round(v / 50.0) * 50)