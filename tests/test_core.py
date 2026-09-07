import unittest


class TestPricing(unittest.TestCase):
    def test_retail_rounds_to_50(self):
        from app.services.pricing import retail_price

        self.assertEqual(retail_price(1000, 35), 1350)
        self.assertEqual(retail_price(1300, 35), 1750)
        self.assertEqual(retail_price(0, 35), 0)

    def test_margin_math(self):
        from app.services.pricing import retail_price

        # 2000 * 1.35 = 2700 ровно
        self.assertEqual(retail_price(2000, 35), 2700)


class TestParser(unittest.TestCase):
    def test_basic_product(self):
        from app.services.parser import parse_product

        p = parse_product("Nike Dunk Low\nРазмеры: 40 41 42 43\nЦена: 7500 руб\nВ наличии: 3 шт")
        self.assertIsNotNone(p)
        self.assertEqual(p.supplier_price, 7500)
        self.assertEqual(p.brand, "Nike")
        self.assertEqual(p.sizes, ["40", "41", "42", "43"])
        self.assertEqual(p.stock, 3)

    def test_no_price_no_product(self):
        from app.services.parser import parse_product

        self.assertIsNone(parse_product("просто текст без цены"))

    def test_letter_sizes(self):
        from app.services.parser import parse_sizes

        self.assertEqual(parse_sizes("Размеры M L XL"), ["M", "L", "XL"])

    def test_detect_brand(self):
        from app.services.parser import detect_brand

        self.assertEqual(detect_brand("Куртка Stone Island 15000"), "Stone Island")
        self.assertIsNone(detect_brand("Ноунейм вещь"))


class TestStylist(unittest.TestCase):
    def test_product_colors(self):
        from app.services.stylist import product_colors

        self.assertIn("white", product_colors("Bape Sta Low White"))
        self.assertIn("black", product_colors("Худи черное оверсайз"))

    def test_photo_color_family(self):
        from app.services.stylist import photo_color_family

        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 10, 10)).save(buf, format="PNG")
        self.assertEqual(photo_color_family(buf.getvalue()), "black")
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (250, 250, 250)).save(buf, format="PNG")
        self.assertEqual(photo_color_family(buf.getvalue()), "white")


if __name__ == "__main__":
    unittest.main()
