"""
Каталогизатор: последние 5000 постов из @optobaza
"""
import asyncio, csv, json, re, os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

PRICE_RE = re.compile(r"(?<!\d)(\d{3,7})(?:\s*₽|\s*руб(?:\.|лей)?|\s*р(?:\.|\b))", re.I)
SIZE_RE = re.compile(r"\b(?:3[5-9]|4[0-9]|5[0-2])(?:[.,]\d)?\b")
STOCK_RE = re.compile(r"(?:в\s*наличии|остаток|шт\.?)\s*[:\-]?\s*(\d+)", re.I)

BRANDS = {
    "nike": "Nike", "air max": "Nike", "dunk": "Nike",
    "jordan": "Nike", "airforce": "Nike", "air force": "Nike", "blazer": "Nike",
    "адидас": "Adidas", "adidas": "Adidas", "campus": "Adidas", "gazelle": "Adidas", "samba": "Adidas",
    "new balance": "New Balance", "550": "New Balance",
    "574": "New Balance", "2002r": "New Balance", "990": "New Balance",
    "puma": "Puma", "reebok": "Reebok", "asics": "Asics",
    "converse": "Converse", "vans": "Vans",
    "newera": "New Era", "new era": "New Era",
    "stussy": "Stussy", "corteiz": "Corteiz", "essentials": "Essentials",
    "trapstar": "Trapstar", "the north face": "The North Face", "tnf": "The North Face",
    "carhartt": "Carhartt", "champion": "Champion", "salomon": "Salomon",
    "under armour": "Under Armour", "on running": "On Running",
    "chrome heart": "Chrome Hearts", "chrome hearts": "Chrome Hearts",
    "balenciaga": "Balenciaga", "gucci": "Gucci", "prada": "Prada",
    "versace": "Versace", "dior": "Dior", "louis vuitton": "Louis Vuitton", "lv": "Louis Vuitton",
    "givenchy": "Givenchy", "valentino": "Valentino", "bottega": "Bottega Veneta",
    "kenzo": "Kenzo", "oakley": "Oakley", "bape": "BAPE", "a bathing ape": "BAPE",
    "cdg": "CDG", "comme des garcons": "CDG", "play cdg": "CDG",
    "off-white": "Off-White", "off white": "Off-White",
    " Palm Angels": "Palm Angels", "palm angels": "Palm Angels",
    "undercover": "Undercover", "saint laurent": "Saint Laurent", "slp": "Saint Laurent",
    "moncler": "Moncler", "canada goose": "Canada Goose",
    "stone island": "Stone Island", "acne": "Acne Studios",
    "rick owens": "Rick Owens", "helmut lang": "Helmut Lang",
    "marcelo burlon": "Marcelo Burlon", "uniqlo": "Uniqlo",
    "zara": "Zara", "h&m": "H&M",
}

CATEGORIES = {
    "кроссов": "Krossovki", "sneaker": "Krossovki", "dunk": "Krossovki",
    "jordan": "Krossovki", "campus": "Krossovki", "air max": "Krossovki",
    "new balance": "Krossovki", "550": "Krossovki", "samba": "Krossovki",
    "gazelle": "Krossovki", "salomon": "Krossovki", "gel-": "Krossovki",
    "худи": "Hoodie", "hoodie": "Hoodie", "свитшот": "Hoodie",
    "лонгслив": "Longsleeve", "лонгслив": "Longsleeve",
    "куртк": "Jacket", "jacket": "Jacket", "бомбер": "Jacket",
    "футболк": "Tee", "tee": "Tee", "t-shirt": "Tee", "поло": "Tee",
    "штан": "Pants", "pant": "Pants", "джоггер": "Pants", "cargo": "Pants",
    "шорты": "Shorts", "шапк": "Hat", "кепк": "Hat", "beret": "Hat",
    "рюкзак": "Bag", "сумк": "Bag",
}

def detect_brand(text):
    t = text.lower()
    for kw, brand in BRANDS.items():
        if kw in t:
            return brand
    # try title-based detection
    lines = text.splitlines()
    if lines:
        title = lines[0].lower()
        for kw, brand in BRANDS.items():
            if kw in title:
                return brand
    return "Other"

def detect_category(text):
    t = text.lower()
    for kw, cat in CATEGORIES.items():
        if kw in t:
            return cat
    return "Other"

def parse_post(text):
    if not text or len(text) < 10:
        return None
    prices = PRICE_RE.findall(text)
    if not prices:
        return None
    price = float(prices[0])
    sizes = sorted(set(SIZE_RE.findall(text)), key=lambda x: float(x.replace(',', '.')))
    stock_match = STOCK_RE.search(text)
    stock = int(stock_match.group(1)) if stock_match else (len(sizes) if sizes else 1)
    # limit stock to reasonable range
    if stock > 100:
        stock = len(sizes) if sizes else 1
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    title = lines[0][:200]
    desc = "\n".join(lines[1:5])[:300]
    return {"title": title, "brand": detect_brand(text), "category": detect_category(text),
            "price": price, "sizes": sizes, "stock": max(stock, 1), "description": desc}

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(
        StringSession(os.getenv('SUPPLIER_SESSION_STRING')),
        int(os.getenv('TELEGRAM_API_ID')),
        os.getenv('TELEGRAM_API_HASH')
    )
    await client.start()
    print("Scanning last 5000 posts from @optobaza...", file=sys.stderr, flush=True)

    products = []
    count = 0
    async for msg in client.iter_messages('optobaza', limit=5000):
        count += 1
        parsed = parse_post(msg.message or '')
        if not parsed:
            continue
        dt = msg.date
        products.append({
            "msg_id": msg.id,
            "date": dt.strftime("%Y-%m-%d") if dt else "",
            **parsed,
            "sizes": ", ".join(parsed["sizes"]) if parsed["sizes"] else "—",
        })
        if count % 1000 == 0:
            print(f"  ...{count} posts, {len(products)} products", file=sys.stderr, flush=True)

    print(f"Done: {count} posts -> {len(products)} products", file=sys.stderr, flush=True)
    await client.disconnect()

    # save CSV
    csv_path = Path("catalog_optobaza.csv")
    fields = ["msg_id", "date", "title", "brand", "category", "price", "sizes", "stock", "description"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(products)
    print(f"CSV: {csv_path}", file=sys.stderr, flush=True)

    # save JSON
    json_path = Path("catalog_optobaza.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"JSON: {json_path}", file=sys.stderr, flush=True)

    # stats
    brands = {}
    cats = {}
    for p in products:
        brands[p["brand"]] = brands.get(p["brand"], 0) + 1
        cats[p["category"]] = cats.get(p["category"], 0) + 1

    print(f"\n=== CATALOG STATS ===", file=sys.stderr, flush=True)
    print(f"Total: {len(products)}", file=sys.stderr, flush=True)
    print(f"\nBrands:", file=sys.stderr, flush=True)
    for b, n in sorted(brands.items(), key=lambda x: -x[1])[:25]:
        print(f"  {b}: {n}", file=sys.stderr, flush=True)
    print(f"\nCategories:", file=sys.stderr, flush=True)
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}", file=sys.stderr, flush=True)
    prices = [p["price"] for p in products if p["price"] > 0]
    if prices:
        print(f"\nPrice: {min(prices):,.0f} - {max(prices):,.0f} (avg {sum(prices)//len(prices):,.0f})", file=sys.stderr, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
