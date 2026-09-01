from __future__ import annotations
from decimal import Decimal
from statistics import median
from dataclasses import dataclass
from .pricing import MarketSnapshot

@dataclass
class MarketOffer:
    source: str
    title: str
    price: Decimal
    url: str | None = None

class MarketProvider:
    async def search(self, brand: str | None, model: str | None, title: str) -> list[MarketOffer]:
        return []

class ImportedMarketProvider(MarketProvider):
    def __init__(self, offers: list[MarketOffer] | None = None):
        self.offers = offers or []

    async def search(self, brand: str | None, model: str | None, title: str) -> list[MarketOffer]:
        needle = ' '.join(x for x in [brand or '', model or '', title] if x).lower()
        words = {w for w in needle.split() if len(w) >= 3}
        return [o for o in self.offers if len(words & set(o.title.lower().split())) >= max(1, min(2, len(words)))]

def summarize(offers: list[MarketOffer]) -> MarketSnapshot:
    if not offers:
        return MarketSnapshot(None, None, None, 0, [])
    prices = [o.price for o in offers]
    return MarketSnapshot(median(prices), min(prices), max(prices), len(prices), sorted(set(o.source for o in offers)))
