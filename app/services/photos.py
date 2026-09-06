import json
import time
from pathlib import Path

import httpx

from app.config import BASE_DIR, get_settings

YA_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
_HREF_TTL = 3600  # 1 час, потом href от Яндекса протухает


class YandexLibrary:
    def __init__(self, index_path: Path, public_key: str):
        self.index_path = index_path
        self.public_key = public_key
        self.by_brand: dict[str, dict] = {}
        self._urls: dict[str, tuple[str, float]] = {}  # path -> (href, timestamp)
        self._client: httpx.AsyncClient | None = None

    def load(self) -> int:
        self.by_brand.clear()
        if not self.index_path.exists():
            return 0
        # пробуем utf-8, если категории битые — чиним mojibake
        try:
            raw = self.index_path.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = self.index_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
        for cat, brands in (data.get("categories") or {}).items():
            # чиним категорию если она оказалась кракозябрами (cp1251 -> utf8)
            if cat and any(ord(c) > 127 and c == "�" for c in cat):
                try:
                    cat = cat.encode("latin1").decode("utf-8")
                except Exception:
                    pass
            for brand, info in (brands or {}).items():
                entry = self.by_brand.setdefault(brand.lower(), {"title": brand, "photos": []})
                entry["photos"].extend(info.get("photos", []))
        return len(self.by_brand)

    @property
    def brand_titles(self) -> list[str]:
        return sorted({v["title"] for v in self.by_brand.values()})

    def paths_for(self, brand: str | None, limit: int = 6) -> list[str]:
        entry = self.by_brand.get((brand or "").lower())
        if not entry:
            return []
        return [p.get("path") for p in entry["photos"][:limit] if p.get("path")]

    async def download_url(self, path: str) -> str | None:
        cached = self._urls.get(path)
        if cached:
            href, ts = cached
            if time.time() - ts < _HREF_TTL:
                return href
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        try:
            r = await self._client.get(YA_API, params={"public_key": self.public_key, "path": path})
            r.raise_for_status()
            href = r.json().get("href")
            if href:
                self._urls[path] = (href, time.time())
            return href
        except Exception:
            # если был кэш но протух — вернём старый как fallback на 5 минут
            if cached:
                return cached[0]
            return None

    async def aclose(self):
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


settings = get_settings()
yandex_library = YandexLibrary(BASE_DIR / "data" / "yandex" / "_yandex_index.json", settings.yandex_public_key)