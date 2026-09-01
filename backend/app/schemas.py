from pydantic import BaseModel
from decimal import Decimal

class ProductIn(BaseModel):
    title: str
    purchase_price: Decimal
    stock: int = 0
    brand: str | None = None
    model: str | None = None
    description: str | None = None
    category: str | None = None

class PriceDecision(BaseModel):
    recommended_price: Decimal
    confidence: float
    reason: str
