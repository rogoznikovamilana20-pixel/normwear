import logging
import os

import httpx

log = logging.getLogger("vkposter")

API = "https://api.vk.ru/method/"
V = "5.199"


def is_configured(settings) -> bool:
    return bool(settings.vk_token and settings.vk_group_id)


async def _call(client: httpx.AsyncClient, method: str, token: str, **params):
    r = await client.post(API + method, data={"access_token": token, "v": V, **params}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"VK {method}: {j['error'].get('error_msg')}")
    return j["response"]


async def post_product(settings, product, brand_title: str | None, photo_refs: list[tuple[str, str]]) -> str | None:
    """Публикует товар на стену сообщества. photo_refs: [(kind, value)] kind=http|file.

    Возвращает ссылку на пост или None.
    """
    if not is_configured(settings):
        return None
    sizes = ", ".join((product.sizes or [])[:14]) or "—"
    name = product.title
    if brand_title and product.model and brand_title.lower() not in product.title.lower():
        name = f"{brand_title} {product.model}"
    text = (
        f"{name}\n\n"
        f"Размеры: {sizes}\n"
        f"Цена: {int(product.retail_price or 0)} ₽\n"
        f"Доставка 2–4 дня\n\n"
        f"Заказ за минуту в телеге: https://t.me/{settings.shop_username}?start=product_{product.id}"
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        attachments: list[str] = []
        # готовим до 4 фото
        files: list[tuple[str, bytes]] = []
        for kind, val in photo_refs[:4]:
            try:
                if kind == "http":
                    r = await client.get(val, timeout=30)
                    r.raise_for_status()
                    if not r.content or len(r.content) < 1024:
                        continue
                    files.append(("photo.jpg", r.content))
                elif kind == "file" and os.path.exists(val):
                    with open(val, "rb") as f:
                        files.append(("photo.jpg", f.read()))
            except Exception as e:
                log.warning("vk photo skip: %s", e)
                continue
        if files:
            up = await _call(client, "photos.getWallUploadServer", settings.vk_token, group_id=settings.vk_group_id)
            saved = []
            # VK принимает по одному файлу за раз
            for fname, data in files:
                ur = await client.post(up["upload_url"], files={"photo": (fname, data, "image/jpeg")}, timeout=60)
                ur.raise_for_status()
                uj = ur.json()
                sv = await _call(
                    client, "photos.saveWallPhoto", settings.vk_token,
                    group_id=settings.vk_group_id, photo=uj["photo"], server=uj["server"], hash=uj["hash"],
                )
                saved.extend(f"photo{p['owner_id']}_{p['id']}" for p in sv)
            attachments = saved
        post = await _call(
            client, "wall.post", settings.vk_token,
            owner_id=-int(settings.vk_group_id),
            message=text,
            attachments=",".join(attachments) if attachments else "",
        )
        return f"https://vk.ru/wall-{settings.vk_group_id}_{post['post_id']}"
