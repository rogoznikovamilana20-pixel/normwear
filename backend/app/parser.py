import re
from dataclasses import dataclass, field

@dataclass
class ParsedProduct:
    title: str
    purchase_price: float
    sizes: list[str] = field(default_factory=list)
    description: str = ''
    brand: str | None = None
    model: str | None = None
    article: str | None = None
    category: str | None = None
    stock: int = 0
    colors: list[str] = field(default_factory=list)

PRICE_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[\s.]\d{3})+|\d{3,7})\s*(?:₽|руб(?:\.|лей)?|р(?:\.|\b))",
    re.I,
)
PRICE_NUM_RE = re.compile(r"(\d{1,3}(?:[\s.]\d{3})+|\d{3,7})")

LETTER_SIZES = re.compile(
    r"\b(XXS|XXL|XXXL|XS|S|M|L|XL|4XL|5XL|6XL)\b",
    re.I,
)
NUMERIC_SIZES = re.compile(
    r"\b(?:3[5-9]|4[0-9]|5[0-6])(?:[.,]\d)?\b",
)
STOCK_RE = re.compile(r"(?:в\s*наличии|остаток|шт\.?)\s*[:\-]?\s*(\d+)", re.I)
ARTICLE_RE = re.compile(r"(?:артикул|article|art\.?|артикул:?)\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.I)
COLOR_RE = re.compile(r"(?:цвет|color)\s*[:\-]?\s*(.+?)(?:\n|$)", re.I)

BRANDS = {
    'nike': 'Nike', 'адидас': 'Adidas', 'adidas': 'Adidas',
    'new balance': 'New Balance', 'puma': 'Puma',
    'reebok': 'Reebok', 'fila': 'Fila', 'asics': 'ASICS',
    'corteiz': 'Corteiz', 'stüssy': 'Stussy', 'stussy': 'Stussy',
    'bape': 'BAPE', 'off-white': 'Off-White', 'off white': 'Off-White',
    'chrome hearts': 'Chrome Hearts', 'chromehearts': 'Chrome Hearts',
    'polar': 'Polar', 'carhartt': 'Carhartt', 'huh': 'HUF',
    'dime': 'Dime', 'neighborhood': 'Neighborhood',
}

SIGNALS = ("размер", "размеры", "в наличии", "цена", "₽", "руб", "артикул", "шт", "optobaza")

def normalize_text(text: str) -> str:
    t = re.sub(r'@\w+', '', text)
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'[ \t]+', ' ', t)
    return t.strip()

def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text)
    if not m:
        return None
    raw = PRICE_NUM_RE.search(m.group(0))
    if not raw:
        return None
    num_str = raw.group(1).replace(' ', '').replace('.', '')
    try:
        return float(num_str)
    except ValueError:
        return None

def parse_sizes(text: str) -> list[str]:
    letter = [s.upper() for s in LETTER_SIZES.findall(text)]
    numeric = sorted(set(NUMERIC_SIZES.findall(text)), key=lambda x: float(x.replace(',', '.')))
    return letter + numeric

def detect_brand(text: str) -> str | None:
    t = text.lower()
    for kw, brand in BRANDS.items():
        if kw in t:
            return brand
    return None

def infer_category(title: str, text: str) -> str:
    t = f"{title} {text}".lower()
    if any(x in t for x in ("кроссов", "sneaker", "dunk", "jordan", "campus", "new balance")):
        return "Кроссовки"
    if any(x in t for x in ("худи", "hoodie", "свитшот", "оуверサイズ")):
        return "Одежда"
    if any(x in t for x in ("куртк", "jacket", "бомбер", "пуховик")):
        return "Верхняя одежда"
    if any(x in t for x in ("футболк", "tee", "t-shirt", "поло")):
        return "Футболки"
    if any(x in t for x in ("штаны", "брюки", "cargo", "джоггеры", "шорты")):
        return "Штаны"
    if any(x in t for x in ("шапк", "beanie", "кепк", "cap")):
        return "Головные уборы"
    if any(x in t for x in ("сумк", "бэг", "bag", "backpack")):
        return "Сумки"
    return "Другое"

def is_probably_product(text: str) -> bool:
    t = text.lower()
    return sum(1 for x in SIGNALS if x in t) >= 2 and parse_price(text) is not None

def parse_product(text: str) -> ParsedProduct | None:
    if not is_probably_product(text):
        return None
    price = parse_price(text)
    if price is None:
        return None
    sizes = parse_sizes(text)
    brand = detect_brand(text)
    article_m = ARTICLE_RE.search(text)
    article = article_m.group(1) if article_m else None
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    title = (lines[0] if lines else "Товар")[:255]
    stock_match = STOCK_RE.search(text)
    stock = int(stock_match.group(1)) if stock_match else (len(sizes) if sizes else 1)
    colors = []
    color_m = COLOR_RE.search(text)
    if color_m:
        colors = [c.strip() for c in color_m.group(1).split(',') if c.strip()]
    return ParsedProduct(
        title=title,
        purchase_price=price,
        sizes=sizes,
        description=normalize_text(text),
        brand=brand,
        model=None,
        article=article,
        category=infer_category(title, text),
        stock=max(stock, 0),
        colors=colors,
    )
