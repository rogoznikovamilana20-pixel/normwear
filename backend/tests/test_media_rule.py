import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import parse_product
from app.services import MediaItem, SupplierPost, select_product_media

def test_first_image_is_not_dropped():
    post = SupplierPost(1, 2, "Nike Dunk\nРазмеры 40-45\n5500 ₽\nВ наличии", [
        MediaItem(1, "first.jpg", "image/jpeg"), MediaItem(2, "second.jpg", "image/jpeg")])
    selected = select_product_media(post)
    assert [x.file_path for x in selected] == ["first.jpg", "second.jpg"]

def test_parser_detects_product():
    p = parse_product("Nike Dunk Low\nРазмеры 40-45\n5500 ₽")
    assert p is not None and p.purchase_price == 5500
