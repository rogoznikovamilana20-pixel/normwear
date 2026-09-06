import re
from dataclasses import dataclass, field

PRICE_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[\s.]\d{3})+|\d{3,7})\s*(?:\u20bd|\u0440\u0443\u0431(?:\.|\u043b\u0435\u0439)?|\u0440(?:\.|\b))", re.I)
PRICE_NUM_RE = re.compile(r"(\d{1,3}(?:[\s.]\d{3})+|\d{3,7})")
LETTER_SIZES = re.compile(r"\b(XXS|XXL|XXXL|3XL|XS|S|M|L|XL|4XL|5XL|6XL)\b", re.I)
NUMERIC_SIZES = re.compile(r"\b(?:3[5-9]|4[0-9]|5[0-6])(?:[.,]\d)?\b")
STOCK_RE = re.compile(r"(?:\u0432\s*\u043d\u0430\u043b\u0438\u0447\u0438\u0438|\u043e\u0441\u0442\u0430\u0442\u043e\u043a|\u0448\u0442\.?)\s*[:\-]?\s*(\d+)", re.I)
ARTICLE_RE = re.compile(r"(?:\u0430\u0440\u0442\u0438\u043a\u0443\u043b|article|art\.?)\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.I)
SIGNALS = ("\u0440\u0430\u0437\u043c\u0435\u0440", "\u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438", "\u0446\u0435\u043d\u0430", "\u20bd", "\u0440\u0443\u0431", "\u0430\u0440\u0442\u0438\u043a\u0443\u043b", "\u0448\u0442", "optobaza", "\u043e\u043f\u0442")

BASE_BRANDS = {
    "nike": "Nike",
    "\u0430\u0434\u0438\u0434\u0430\u0441": "Adidas",
    "adidas": "Adidas",
    "new balance": "New Balance",
    "puma": "Puma",
    "reebok": "Reebok",
    "fila": "Fila",
    "asics": "ASICS",
    "jordan": "Jordan",
    "yeezy": "Yeezy",
    "converse": "Converse",
    "vans": "Vans",
    "corteiz": "Corteiz",
    "stussy": "Stussy",
    "bape": "BAPE",
    "off-white": "Off-White",
    "carhartt": "Carhartt",
    "huf": "HUF",
    "dime": "Dime",
    "the north face": "The North Face",
    "stone island": "Stone Island",
    "ralph lauren": "Ralph Lauren",
    "lacoste": "Lacoste",
    "fred perry": "Fred Perry",
    "hugo boss": "Hugo Boss",
    "tommy hilfiger": "Tommy Hilfiger",
    "guess": "Guess",
    "diesel": "Diesel",
    "moncler": "Moncler",
    "canada goose": "Canada Goose",
    "columbia": "Columbia",
    "timberland": "Timberland",
    "dr.martens": "Dr. Martens",
    "crocs": "Crocs",
}


@dataclass
class ParsedProduct:
    title: str
    supplier_price: float
    sizes: list[str] = field(default_factory=list)
    description: str = ""
    brand: str | None = None
    article: str | None = None
    category: str | None = None
    stock: int = 1


def normalize_text(text: str) -> str:
    t = re.sub(r"@\w+", "", text)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text)
    if not m:
        return None
    raw = PRICE_NUM_RE.search(m.group(0))
    if not raw:
        return None
    num_str = raw.group(1).replace(" ", "").replace(".", "")
    try:
        return float(num_str)
    except ValueError:
        return None


def parse_sizes(text: str) -> list[str]:
    letter = [s.upper() for s in LETTER_SIZES.findall(text)]
    numeric = sorted(set(NUMERIC_SIZES.findall(text)), key=lambda x: float(x.replace(",", ".")))
    return letter + numeric


def detect_brand(text: str, known_brands=None) -> str | None:
    t = text.lower()
    matches = []
    for kw, brand in BASE_BRANDS.items():
        if kw in t:
            matches.append((len(kw), brand))
    for name in known_brands or []:
        low = name.lower()
        if low in t:
            matches.append((len(low), name))
    if not matches:
        return None
    return max(matches)[1]


def infer_category(title: str, text: str) -> str:
    t = f"{title} {text}".lower()
    if any(x in t for x in ("\u043a\u0440\u043e\u0441\u0441\u043e\u0432", "sneaker", "dunk", "jordan", "campus")):
        return "\u041a\u0440\u043e\u0441\u0441\u043e\u0432\u043a\u0438"
    if any(x in t for x in ("\u0445\u0443\u0434\u0438", "hoodie", "\u0441\u0432\u0438\u0442\u0448\u043e\u0442")):
        return "\u041e\u0434\u0435\u0436\u0434\u0430"
    if any(x in t for x in ("\u043a\u0443\u0440\u0442\u043a", "jacket", "\u0431\u043e\u043c\u0431\u0435\u0440", "\u043f\u0443\u0445\u043e\u0432\u0438\u043a")):
        return "\u0412\u0435\u0440\u0445\u043d\u044f\u044f \u043e\u0434\u0435\u0436\u0434\u0430"
    if any(x in t for x in ("\u0444\u0443\u0442\u0431\u043e\u043b\u043a", "tee", "t-shirt")):
        return "\u0424\u0443\u0442\u0431\u043e\u043b\u043a\u0438"
    if any(x in t for x in ("\u0448\u0442\u0430\u043d\u044b", "\u0431\u0440\u044e\u043a\u0438", "cargo", "\u0434\u0436\u043e\u0433\u0433\u0435\u0440\u044b", "\u0448\u043e\u0440\u0442\u044b")):
        return "\u0428\u0442\u0430\u043d\u044b"
    if any(x in t for x in ("\u0448\u0430\u043f\u043a", "beanie", "\u043a\u0435\u043f\u043a")):
        return "\u0413\u043e\u043b\u043e\u0432\u043d\u044b\u0435 \u0443\u0431\u043e\u0440\u044b"
    if any(x in t for x in ("\u0441\u0443\u043c\u043a", "bag", "backpack")):
        return "\u0421\u0443\u043c\u043a\u0438"
    return "\u0414\u0440\u0443\u0433\u043e\u0435"


def is_probably_product(text: str) -> bool:
    t = text.lower()
    return sum(1 for x in SIGNALS if x in t) >= 2 and parse_price(text) is not None


def parse_product(text: str, known_brands=None) -> ParsedProduct | None:
    if not text or not is_probably_product(text):
        return None
    price = parse_price(text)
    if price is None:
        return None
    sizes = parse_sizes(text)
    article_m = ARTICLE_RE.search(text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    title = (lines[0] if lines else "\u0422\u043e\u0432\u0430\u0440")[:255]
    stock_m = STOCK_RE.search(text)
    stock = int(stock_m.group(1)) if stock_m else (len(sizes) if sizes else 1)
    return ParsedProduct(
        title=title,
        supplier_price=price,
        sizes=sizes,
        description=normalize_text(text)[:2000],
        brand=detect_brand(text, known_brands),
        article=article_m.group(1) if article_m else None,
        category=infer_category(title, text),
        stock=max(stock, 0),
    )