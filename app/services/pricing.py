def retail_price(supplier_price: float, margin_pct: float) -> int:
    v = supplier_price * (100.0 + margin_pct) / 100.0
    return int(round(v / 50.0) * 50)


# Опт от 10 шт: скидка поставщика за объём + наша уменьшенная наценка.
OPT_SUPPLIER_DISCOUNT = 0.10
OPT_MARGIN_PCT = 12.0
OPT_MIN_QTY = 10
# Лестница объёма: (мин. суммарное кол-во, доп. скидка с опт-цены).
# Запас маржи ~10-12%, поэтому суммарная доп. скидка не должна превышать 0.05.
OPT_TIERS: tuple[tuple[int, float], ...] = ((10, 0.0), (30, 0.02), (50, 0.03))


def opt_price(supplier_price: float, supplier_discount: float = OPT_SUPPLIER_DISCOUNT, margin_pct: float = OPT_MARGIN_PCT) -> int:
    return retail_price(supplier_price * (1 - supplier_discount), margin_pct)


def opt_tier_discount(total_qty: int) -> float:
    best = 0.0
    for need, extra in OPT_TIERS:
        if total_qty >= need:
            best = extra
    return best


def opt_price_for_qty(supplier_price: float, total_qty: int) -> int:
    base = opt_price(supplier_price)
    return int(round(base * (1 - opt_tier_discount(total_qty)) / 50.0) * 50)