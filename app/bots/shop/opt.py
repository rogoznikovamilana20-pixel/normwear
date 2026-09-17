"""Опт от 10 шт: /opt — условия, контакт, строки «артикул размер кол-во», заявка админам."""
import html

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bots.shop import OptOrder, main_menu, router, settings
from app.db import SessionMaker
from app.models import Order, OrderItem, Product, User
from app.services import notify, opt as opt_svc, orders
from app.services.pricing import OPT_MIN_QTY, opt_price, opt_price_for_qty, opt_tier_discount

TERMS = (
    "🏭 <b>Опт NORMWEAR — от 10 шт</b> (можно разные модели)\n\n"
    "• Цены — по запросу: пришли заявку, менеджер вернётся с ценами и реквизитами\n"
    "• 100% предоплата на СБП, после неё подтверждаем размеры и выкупаем\n"
    "• Брак — только по фото в день получения\n"
    "• Доставка за твой счёт, отправляем в день выкупа\n\n"
    "Жми «📝 Оформить заявку» и пришли позиции одним сообщением."
)


def parse_opt_lines(text: str, catalog: dict[str, Product]) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            errors.append(f"«{line}» — формат: АРТИКУЛ РАЗМЕР КОЛ-ВО")
            continue
        try:
            qty = int(parts[-1])
        except ValueError:
            errors.append(f"«{line}» — кол-во должно быть числом")
            continue
        if qty < 1:
            errors.append(f"«{line}» — кол-во минимум 1")
            continue
        size = parts[-2].strip()
        article = " ".join(parts[:-2]).upper()
        p = catalog.get(article)
        if p is None:
            errors.append(f"«{article}» — нет в номенклатуре")
            continue
        avail = [str(x).strip() for x in (p.sizes or [])]
        if avail and size not in avail:
            errors.append(f"«{line}» — такого размера нет, есть: {', '.join(avail[:14])}")
            continue
        price = opt_price(float(p.supplier_price or 0))
        items.append({"article": article, "title": p.title, "size": size, "qty": qty, "price": price, "pid": p.id})
    return items, errors


@router.message(Command("opt"))
async def cmd_opt(message: Message, state: FSMContext):
    await state.clear()
    async with SessionMaker() as s:
        await orders.get_or_create_user(s, message.from_user)
        await s.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Оформить заявку", callback_data="opt_go")],
    ])
    await message.answer(TERMS + f"\n\n📶 <b>Скидки объёма:</b> {opt_svc.tiers_text()}", reply_markup=kb)
    try:
        await message.answer_document(
            BufferedInputFile(await opt_svc.nomenclature_csv(), filename="nomenklatura.csv"),
            caption="📄 Номенклатура: артикулы и размеры (цены — по запросу)",
        )
    except Exception:
        pass


@router.callback_query(F.data == "opt_go")
async def cb_opt_go(cb: CallbackQuery, state: FSMContext):
    await state.set_state(OptOrder.contact)
    await cb.message.answer("👤 Пришли контакт одним сообщением: <b>имя и телефон</b> — менеджер свяжется по предоплате.")
    await cb.answer()


@router.message(OptOrder.contact, StateFilter(OptOrder.contact), F.text & ~F.text.startswith("/"))
async def opt_contact(message: Message, state: FSMContext):
    contact = (message.text or "").strip()[:140]
    if len(contact) < 5:
        await message.answer("Слишком коротко — пришли имя и телефон.")
        return
    await state.update_data(contact=contact)
    await state.set_state(OptOrder.lines)
    await message.answer(
        "📝 Теперь пришли позиции <b>одним сообщением</b>, каждую с новой строки:\n"
        "<code>АРТИКУЛ РАЗМЕР КОЛ-ВО</code>\n\n"
        "Пример:\n<code>OPTO32448 M 5\nNW-112 L 5</code>\n\n"
        f"Минимум суммарно — {OPT_MIN_QTY} шт."
    )


@router.message(OptOrder.lines, StateFilter(OptOrder.lines), F.text & ~F.text.startswith("/"))
async def opt_lines(message: Message, state: FSMContext):
    catalog = await opt_svc.article_catalog()
    items, errors = parse_opt_lines(message.text or "", catalog)
    if errors:
        await message.answer("⚠️ Не смог разобрать:\n" + "\n".join(f"• {html.escape(e)}" for e in errors[:10]) + "\n\nПришли весь список заново, одним сообщением.")
        return
    if not items:
        await message.answer("Пусто — пришли позиции списком.")
        return
    total_qty = sum(i["qty"] for i in items)
    if total_qty < OPT_MIN_QTY:
        await message.answer(f"Сейчас {total_qty} шт, а минимум — {OPT_MIN_QTY}. Добавь позиции и пришли список заново.")
        return
    for i in items:
        base = float(catalog[i["article"]].supplier_price or 0)
        i["price"] = opt_price_for_qty(base, total_qty)
    await state.update_data(items=items)
    lines = [f"• {html.escape(i['article'])} · {html.escape(i['size'])} × {i['qty']}" for i in items]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data="opt_ok")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])
    await message.answer(
        f"📋 <b>Твоя заявка ({total_qty} шт):</b>\n\n" + "\n".join(lines) + "\n\nЦены менеджер пришлёт после проверки наличия.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "opt_ok")
async def cb_opt_ok(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    user_id = state.key.user_id
    items = d.get("items") or []
    contact = d.get("contact") or ""
    await state.clear()
    if not items:
        await cb.message.answer("Заявка пустая — начни заново: /opt")
        await cb.answer()
        return
    total_qty = sum(i["qty"] for i in items)
    total = sum(i["qty"] * i["price"] for i in items)
    tier = opt_tier_discount(total_qty)
    tier_note = f"−{int(tier * 100)}% ({total_qty} шт)" if tier else f"база ({total_qty} шт)"
    async with SessionMaker() as s:
        user = await s.get(User, user_id)
        uname = f"@{user.username}" if user and user.username else "без юзернейма"
        order = Order(
            user_id=user_id, status="awaiting_delivery", is_wholesale=True,
            subtotal=float(total), total=float(total), full_name="ОПТ-заявка",
            comment="[OPT] " + contact + "\n" + "\n".join(f"{i['article']} {i['size']} x{i['qty']}" for i in items),
        )
        s.add(order)
        await s.flush()
        for i in items:
            s.add(OrderItem(order_id=order.id, product_id=i["pid"], title=i["title"], size=i["size"], price=float(i["price"]), qty=i["qty"]))
        await s.commit()
        oid = order.id
    await cb.message.answer(
        f"✅ <b>Оптовая заявка №{oid} отправлена!</b>\nМенеджер проверит наличие, пришлёт цены и реквизиты СБП.",
        reply_markup=main_menu(),
    )
    await cb.answer()
    lines = [f"• {html.escape(i['article'])} · {html.escape(i['size'])} × {i['qty']} — {i['qty'] * i['price']} ₽" for i in items]
    admin_text = (
        f"🏭 <b>Новая ОПТ-заявка №{oid}</b>\n"
        f"👤 {html.escape(contact)} · {uname} · id <code>{user_id}</code>\n"
        f"📶 Ступень объёма: {tier_note}\n\n"
        + "\n".join(lines)
        + f"\n\n💰 <b>Ориентир: {total} ₽</b> (цену называет менеджер)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Открыть заказ", callback_data=f"od:{oid}")],
        [InlineKeyboardButton(text="➡ Готов к оплате", callback_data=f"od:{oid}:next")],
    ])
    await notify.notify_admins(admin_text, kb)
