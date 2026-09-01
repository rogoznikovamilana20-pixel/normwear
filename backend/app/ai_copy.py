from html import escape

def beautify_post(title: str, description: str, price: float, sizes: list[str]) -> str:
    size_line = ', '.join(sizes) if sizes else 'уточняйте наличие'
    return (
        f'🔥 <b>{escape(title)}</b>\n\n'
        f'{escape(description[:700])}\n\n'
        f'Размеры: <b>{escape(size_line)}</b>\n'
        f'Цена: <b>{price:,.0f} ₽</b>\n\n'
        '📦 В наличии\n'
        '🛍 <b>Заказать — кнопка ниже</b>'
    )
