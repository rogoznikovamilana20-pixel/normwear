import io
import logging

log = logging.getLogger("stylist")

# семейство -> токены в названиях (ru/en)
COLORS = {
    "black": ("черн", "black", "nero", "noir"),
    "white": ("бел", "white", "blanc", "bianco"),
    "grey": ("сер", "grey", "gray", "grigio"),
    "blue": ("син", "blue", "blu", "navy"),
    "red": ("красн", "red", "rosso", "rouge"),
    "green": ("зелен", "green", "verde", "olive", "хаки"),
    "beige": ("беж", "beige", "cream", "sand", "крем"),
    "brown": ("коричн", "brown", "marrone", "шоколад"),
    "pink": ("розов", "pink", "rosa"),
}


def photo_color_family(image_bytes: bytes) -> str | None:
    try:
        from PIL import Image, ImageStat
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((64, 64))
        stat = ImageStat.Stat(img)
        r, g, b = stat.mean
        mx, mn = max(r, g, b), min(r, g, b)
        if mx - mn < 18:
            return "white" if mx > 200 else ("black" if mx < 70 else "grey")
        if r > g and r > b:
            return "red" if r - max(g, b) > 25 else "brown"
        if g >= r and g >= b:
            return "green"
        if b > r and b > g:
            return "blue" if b - max(r, g) > 20 else "grey"
        if r > 150 and g > 120 and b < 130:
            return "beige"
        return "grey"
    except Exception as e:
        log.warning("color failed: %s", e)
        return None


def product_colors(title: str, description: str = "") -> set[str]:
    t = f"{title} {description}".lower()
    return {fam for fam, toks in COLORS.items() if any(tok in t for tok in toks)}


async def match_products(session, image_bytes: bytes, limit: int = 3):
    from sqlalchemy import select

    from app.models import Product

    fam = await __import__("asyncio").to_thread(photo_color_family, image_bytes)
    prods = (await session.scalars(select(Product).where(Product.status == "published").order_by(Product.id.desc()).limit(30))).all()
    if not prods:
        return [], None
    if fam is None:
        return prods[:limit], None
    scored = []
    for p in prods:
        cols = product_colors(p.title or "", p.description or "")
        scored.append((0 if fam in cols else 1, p))
    scored.sort(key=lambda x: x[0])
    return [p for _, p in scored[:limit]], fam
