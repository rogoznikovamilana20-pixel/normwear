"""Общее для опта: каталог по артикулам, номенклатура без цен, прайс с лестницей."""
import csv
import io

from sqlalchemy import select

from app.db import SessionMaker
from app.models import Brand, Product
from app.services.pricing import OPT_TIERS, opt_price, opt_price_for_qty


async def article_catalog() -> dict[str, Product]:
    async with SessionMaker() as s:
        prods = (await s.scalars(select(Product).where(Product.status == "published"))).all()
        brands = {}
        out = {}
        for p in prods:
            if float(p.supplier_price or 0) <= 0:
                continue
            if p.brand_id and p.brand_id not in brands:
                b = await s.get(Brand, p.brand_id)
                brands[p.brand_id] = b.title if b else ""
            p._brand_title = brands.get(p.brand_id or 0, "")
            out[(p.article or f"NW-{p.id}").upper()] = p
    return out


def _sizes(p: Product) -> str:
    return ", ".join(str(x) for x in (p.sizes or []))


async def nomenclature_csv() -> bytes:
    """Артикул/название/размеры без цен — оптовику."""
    cat = await article_catalog()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Артикул", "Бренд", "Товар", "Размеры"])
    for art in sorted(cat):
        p = cat[art]
        w.writerow([art, getattr(p, "_brand_title", ""), p.title, _sizes(p)])
    return buf.getvalue().encode("utf-8-sig")


async def pricelist_csv() -> bytes:
    """Внутренний прайс менеджера: опт по ступеням + розница."""
    cat = await article_catalog()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Артикул", "Бренд", "Товар", "Размеры", "Опт 10+", "Опт 30+", "Опт 50+", "Розница"])
    for art in sorted(cat):
        p = cat[art]
        base = float(p.supplier_price or 0)
        w.writerow([
            art, getattr(p, "_brand_title", ""), p.title, _sizes(p),
            opt_price(base), opt_price_for_qty(base, 30), opt_price_for_qty(base, 50),
            int(p.retail_price or 0),
        ])
    return buf.getvalue().encode("utf-8-sig")


def tier_lines(base: float) -> str:
    return " · ".join(f"{need}+: {opt_price_for_qty(base, need)}₽" for need, _ in OPT_TIERS)


def tiers_text() -> str:
    return ", ".join(f"{need}+ −{int(extra * 100)}%" if extra else f"{need}+ база" for need, extra in OPT_TIERS)
