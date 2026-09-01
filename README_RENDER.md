# Deploy to Render (free eternal https)

1. Push this folder to GitHub:
```
git init
git add .
git commit -m "normwear v0.4"
git branch -M main
git remote add origin https://github.com/YOURNAME/normwear-shop.git
git push -u origin main
```
2. https://dashboard.render.com -> New -> Blueprint -> Connect repo -> select normwear-shop
3. Render will read render.yaml and create 3 services: normwear-api (web), normwear-redis, normwear-db
4. In dashboard -> normwear-api -> Environment -> add:
SHOP_BOT_TOKEN=8927899357:AAG205Gt2edhwz-tjlVvquQiW75sOK2ejXU
ADMIN_BOT_TOKEN=8773180141:AAHG1j0A3IDfh8PlLt0ceO5uyE54V4qP-Y0
TELEGRAM_API_ID=31083034
TELEGRAM_API_HASH=c72238c473a18fcceb325f062ee3a56a
SUPPLIER_SESSION_STRING=1ApWapzM... (from .env)
5. Deploy -> wait 3 min -> https://normwear-api.onrender.com/health -> https://normwear-api.onrender.com/app/
6. Update MINIAPP_URL_TEMPLATE to https://normwear-api.onrender.com/app/?product={product_id} and set in BotFather.

Free tier sleeps after 15m inactivity, wakes in 30s. DuckDNS normwear7776.duckdns.org can be CNAME to onrender.com for custom domain.

TryCloudflare tunnel https://shaved-solve-beneficial-chemistry.trycloudflare.com remains for local test.
