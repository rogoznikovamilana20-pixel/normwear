# Деплой NORMWEAR на бесплатный стабильный сервер

## Вариант 1 — Render (как в render.yaml, бесплатно но спит)
1. Создай репозиторий на GitHub **только** из папки `tg-bot` (не из `projects`):
   - `cd C:\Users\andre\projects\tg-bot`
   - `git init` (если нет .git внутри), `git add .`, `git commit -m "init"`
   - Создай пустой репо на github.com → `git remote add origin https://github.com/USERNAME/normwear.git` → `git push -u origin master`
2. Render → New → Blueprint → выбери репо → применит `render.yaml` (создаст web + postgres).
3. В Render → Environment вставь секреты: `SHOP_BOT_TOKEN`, `ADMIN_BOT_TOKEN`, `SHOP_CHANNEL_ID=-1004387729213`, `ADMIN_TELEGRAM_IDS=1977604257`
4. Deploy → логи должны показать `Yandex library loaded: 38 brands`, `Shop bot polling as @norm_shop_bot`
5. Чтобы не спал: UptimeRobot.com → Create Monitor → HTTP(s) → URL `https://normwear-shop.onrender.com/healthz` → каждые 5 мин.

## Вариант 2 — Koyeb / Railway / Fly (не спят, рекомендую)
- Koyeb: `koyeb app create normwear --docker . --port 8000 --env PORT=8000` + env vars
- Railway: `railway up` из папки, добавит Postgres автоматом
- Fly: `fly launch --dockerfile Dockerfile` + `fly secrets set SHOP_BOT_TOKEN=...`

## Вариант 3 — Дома (сейчас duckdns)
- `start.bat` держит бота 24/7, но нужен проброс порта 8000 и `PORT` в `.env`. Для бизнеса лучше VPS.

## Проверка после деплоя
- `https://xxx.onrender.com/healthz` → `{"ok":true}`
- `https://xxx.onrender.com/api/catalog` → 29 товаров
- `@norm_shop_bot` /start → каталог с фото Я.Диска

## Бэкап БД (SQLite)
- Локально: `copy normwear.db backups\normwear_2026-09-06.db`
- На Render Postgres: `pg_dump $DATABASE_URL > backup.sql` (добавь Cron в Render)

## Важно
- `.env` никогда не коммить (уже в `.gitignore`)
- `SHOP_CHANNEL_ID` и токены — только в env vars сервера
- После деплоя удали демо `DEMO*` если залил реальные: `DELETE FROM products WHERE article LIKE 'DEMO%'`
