from html import escape

def beautify_post(title: str, description: str, price: float, sizes: list[str]) -> str:
    size_line = ', '.join(sizes) if sizes else 'уточняйте'
    return (
        f'🔥 <b>{escape(title)}</b>\n\n'
        f'{escape(description[:700])}\n\n'
        f'📐 Размерный ряд: <b>{escape(size_line)}</b>\n'
        f'🚚 Отправка из Москвы\n\n'
        f'💰 Цена: <b>{price:,.0f} ₽</b>\n\n'
        f'👜 Купить в боте: @norm_shop_bot\n\n'
        f'— — —\n\n'
        f'📦 Доставка по всей России 🇷🇺\n'
        f'💳 Оплата при получении ✅'
    )

def manual_post(title: str, brand: str, description: str, price: float, sizes: list[str], material: str = '', badge: str = '') -> str:
    size_line = ', '.join(sizes) if sizes else 'уточняйте'
    lines = [f'🔥 <b>{escape(title)}</b>\n']
    if description:
        lines.append(f'{escape(description)}\n')
    lines.append(f'📐 Размерный ряд: <b>{escape(size_line)}</b>')
    if material:
        lines.append(f'📦 Материал: {escape(material)}')
    lines.append(f'🚚 Отправка из Москвы\n')
    lines.append(f'💰 Цена: <b>{price:,.0f} ₽</b>\n')
    lines.append(f'👜 Купить в боте: @norm_shop_bot\n')
    lines.append(f'— — —\n')
    lines.append(f'📦 Доставка по всей России 🇷🇺')
    lines.append(f'💳 Оплата при получении ✅')
    return '\n'.join(lines)
