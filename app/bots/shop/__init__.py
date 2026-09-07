from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import get_settings

router = Router()
settings = get_settings()

MENU = {"🛍 Каталог", "🛒 Корзина", "☰ Меню", "📦 Мои заказы", "🎁 Бонусы", "📞 Поддержка", "🔔 Дропы", "🎡 Колесо", "📦 BOX"}

WELCOME = (
    "👋 Добро пожаловать в <b>NORMWEAR</b>\n"
    "\n"
    "Стритвир и кроссовки топовых брендов.\n"
    "💰 Честные цены · 🚚 Доставка 2–4 дня · ✅ Проверка перед отправкой\n"
    "\n"
    "Выбирай раздел внизу и собирай корзину. Бонусы начисляются с каждого заказа."
)


def _miniapp_url() -> str | None:
    # Только публичный https подходит для Telegram WebApp.
    # Шаблон вида https://host/app/?product={id} -> корень https://host/
    tpl = (settings.miniapp_url_template or "").strip()
    if tpl:
        base = tpl.split("?")[0].replace("/app/", "/").rstrip("/") + "/"
        if base.startswith("https://"):
            return base
    # canonical fallback — Render (когда поднимется, кнопки сразу оживут)
    return "https://normwear-shop.onrender.com/"


def miniapp_available() -> bool:
    # Проверять жив ли хост здесь не будем — ТГ сам покажет ошибку.
    # Кнопку показываем всегда, чтобы мини-апп был виден в боте/канале.
    return True


def main_menu() -> ReplyKeyboardMarkup:
    # компактно: только 3 кнопки внизу, остальное — inline в чате
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="🛒 Корзина")],
            [KeyboardButton(text="☰ Меню")],
        ],
        resize_keyboard=True,
    )


def menu_inline() -> InlineKeyboardMarkup:
    miniapp = _miniapp_url()
    rows: list[list[InlineKeyboardButton]] = []
    # Мини-каталог всегда первой строкой: WebApp если https, иначе ссылка на бота
    try:
        from aiogram.types import WebAppInfo as _WAI

        if miniapp and miniapp.startswith("https://"):
            rows.append([InlineKeyboardButton(text="✨ Мини-каталог", web_app=_WAI(url=miniapp))])
        else:
            rows.append([InlineKeyboardButton(text="✨ Мини-каталог", url=f"https://t.me/{settings.shop_username}?start=catalog")])
    except Exception:
        pass
    rows += [
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="m_orders"), InlineKeyboardButton(text="🎁 Бонусы", callback_data="m_bonus")],
        [InlineKeyboardButton(text="🔔 Дропы", callback_data="m_drops"), InlineKeyboardButton(text="🎡 Колесо", callback_data="m_wheel")],
        [InlineKeyboardButton(text="📦 BOX", callback_data="m_box"), InlineKeyboardButton(text="🤍 Избранное", callback_data="m_fav")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="m_support")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_menu(target: Message):
    await target.answer("☰ <b>Меню NORMWEAR</b>\nВыбери раздел:", reply_markup=menu_inline())


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])


class Checkout(StatesGroup):
    name = State()
    phone = State()
    city = State()
    address = State()
    promo = State()
    confirm = State()


class ReviewFSM(StatesGroup):
    text = State()


class StockReq(StatesGroup):
    size = State()


pending_review: dict[int, int] = {}
pending_stock_product: dict[int, int] = {}



from app.bots.shop import catalog as _catalog  # noqa: F401
from app.bots.shop import cart as _cart  # noqa: F401
from app.bots.shop import loyalty as _loyalty  # noqa: F401
from app.bots.shop import reviews as _reviews  # noqa: F401
from app.bots.shop import search as _search  # noqa: F401
from app.bots.shop import menu as _menu  # noqa: F401
