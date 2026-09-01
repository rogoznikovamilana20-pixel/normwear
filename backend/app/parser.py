import re
from dataclasses import dataclass

@dataclass
class ParsedProduct:
    title: str
    purchase_price: float
    sizes: list[str]
    description: str
    brand: str | None
    model: str | None
    category: str | None
    stock: int

PRICE_RE = re.compile(r"(?<!\d)(\d{3,7})(?:\s*₽|\s*руб(?:\.|лей)?|\s*р(?:\.|\b)|\s*$)", re.I)
SIZE_RE = re.compile(r"\b(?:3[5-9]|4[0-9]|5[0-2])(?:[.,]\d)?\b")
STOCK_RE = re.compile(r"(?:в\s*наличии|остаток|шт\.?)\s*[:\-]?\s*(\d+)", re.I)

SIGNALS = ("размер", "размеры", "в наличии", "цена", "₽", "руб", "артикул", "шт")

def is_probably_product(text: str) -> bool:
    t = text.lower()
    return sum(x in t for x in SIGNALS) >= 2 and bool(PRICE_RE.search(t))

def infer_category(title: str, text: str) -> str:
    t = f"{title} {text}".lower()
    if any(x in t for x in ("кроссов", "sneaker", "dunk", "jordan", "campus", "new balance")):
        return "Кроссовки"
    if any(x in t for x in ("худи", "hoodie", "свитшот")):
        return "Одежда"
    if any(x in t for x in ("куртк", "jacket", "бомбер", "пуховик")):
        return "Верхняя одежда"
    if any(x in t for x in ("футболк", "tee", "t-shirt")):
        return "Футболки"
    return "Другое"

def parse_product(text: str) -> ParsedProduct | None:
    if not is_probably_product(text):
        return None
    price_match = PRICE_RE.search(text)
    if not price_match:
        return None
    price = float(price_match.group(1))
    sizes = sorted(set(SIZE_RE.findall(text)), key=lambda x: float(x.replace(',', '.')))
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    title = (lines[0] if lines else "Товар")[:255]
    stock_match = STOCK_RE.search(text)
    stock = int(stock_match.group(1)) if stock_match else (len(sizes) if sizes else 1)
    return ParsedProduct(
        title=title,
        purchase_price=price,
        sizes=sizes,
        description=text.strip(),
        brand=None,
        model=None,
        category=infer_category(title, text),
        stock=max(stock, 0),
    )
