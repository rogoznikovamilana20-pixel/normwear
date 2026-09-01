from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

@dataclass
class MarketSnapshot:
    median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    sample_size: int
    sources: list[str]

@dataclass
class PriceDecision:
    price: Decimal
    confidence: float
    reason: str

def round_price(value: Decimal) -> Decimal:
    return (value / Decimal('10')).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * Decimal('10')

def recommend_price(purchase_price: Decimal, margin_pct: Decimal, market: MarketSnapshot | None, extra_costs: Decimal = Decimal('0')) -> PriceDecision:
    floor = purchase_price + extra_costs
    floor = floor / (Decimal('1') - margin_pct / Decimal('100'))
    if not market or market.median is None or market.sample_size < 3:
        return PriceDecision(round_price(floor), 0.45, 'Недостаточно рыночных данных; использована целевая маржа.')
    candidate = min(market.median * Decimal('0.985'), market.median)
    if candidate < floor:
        candidate = floor
    confidence = min(0.98, 0.55 + market.sample_size / 100)
    reason = f"Медиана рынка {market.median} ₽; выборка {market.sample_size}; источники: {', '.join(market.sources) or 'нет'}."
    return PriceDecision(round_price(candidate), confidence, reason)
