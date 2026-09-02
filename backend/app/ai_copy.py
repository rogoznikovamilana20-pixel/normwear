from html import escape

def beautify_post(title: str, description: str, price: float, sizes: list[str]) -> str:
    size_line = ', '.join(sizes) if sizes else 'уточняйте наличие'
    return (
        f'🔥 <b>{escape(title)}</b>\n\n'
        f'{escape(description[:700])}\n\n'
        f'📐 Размеры: <b>{escape(size_line)}</b>\n'
        f'💰 Цена: <b>{price:,.0f} ₽</b>\n\n'
        f'📦 В наличии · Быстрая доставка\n'
        f'🔄 Возврат 14 дней\n\n'
        f'🛍 <b>Заказать — кнопка ниже ↓</b>'
    )

def manual_post(title: str, brand: str, description: str, price: float, sizes: list[str], badge: str = '') -> str:
    size_line = ', '.join(sizes) if sizes else 'уточняйте'
    badge_line = f'\n{badge}\n' if badge else ''
    return (
        f'🏷 <b>{escape(brand)}</b>\n'
        f'🔥 <b>{escape(title)}</b>\n'
        f'{badge_line}\n'
        f'{escape(description)}\n\n'
        f'📐 Размеры: <b>{escape(size_line)}</b>\n'
        f'💰 <b>{price:,.0f} ₽</b>\n\n'
        f'✅ Качество 1к1 как оригинал · В наличии\n'
        f'📦 Доставка 1-3 дня · Возврат 14 дней\n\n'
        f'🛍 <b>Купить — нажми кнопку ↓</b>'
    )
