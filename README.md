# NORMWEAR SHOP

Telegram commerce stack for:
- @norm_shop_bot — customer bot
- @normal_admin_bot — admin bot
- @normwear_shop — storefront channel
- supplier: https://t.me/optobaza

## Architecture

- FastAPI backend
- aiogram 3 bots
- PostgreSQL
- Redis
- Celery worker
- Telegram Mini App (React + Vite)
- Supplier ingestion worker (MTProto adapter boundary)
- Market/pricing engine adapter boundary
- Channel publisher
- Docker Compose

## Important security note

Bot tokens and Telegram API secrets are intentionally NOT included.
Put them in `.env` locally. Because the tokens were exposed in chat, rotate both BotFather tokens before production.

## First run

1. Copy `.env.example` to `.env`
2. Fill secrets and database values.
3. `docker compose up --build`
4. Open API docs at `http://localhost:8000/docs`
5. Build the Mini App with `cd miniapp && npm install && npm run build`
6. Configure the resulting Mini App URL in BotFather.

The supplier MTProto adapter intentionally requires a user session/API credentials and is not populated with secrets.


## Supplier media rule

There is NO rule that the first image in a supplier post is a logo.
All media is treated as product media by default. The ingestion layer may
exclude an image only when content-aware detection identifies it as the
supplier's channel avatar/logo with high confidence (for example via exact
hash/perceptual hash or visual similarity). When uncertain, the image is kept.

## Supplier photo handling — corrected

The supplier channel avatar/logo is NOT assumed to be the first photo of a post.
The importer preserves every image attached to a supplier post or album. No
positional deletion is performed. If later a repeated supplier watermark/logo
appears as actual post media, it should be removed only by a dedicated
content-aware detector with high confidence; uncertainty means keep the image.

## Mini App

The Mini App is served by the API at `/app`. Put the public HTTPS URL into
`MINIAPP_URL_TEMPLATE`, e.g. `https://shop.example/app/?product={product_id}`.
Telegram BotFather should be configured with the same Main Mini App URL.
