# frontend build stage - builds React Vite app if present, otherwise keeps vanilla app.js
FROM node:20-alpine AS frontend
WORKDIR /build/miniapp
COPY miniapp/package.json ./
COPY miniapp/vite.config.ts ./
COPY miniapp/tsconfig.json ./
COPY miniapp/index.html ./
COPY miniapp/style.css ./
COPY miniapp/src ./src
COPY miniapp/app.js ./
RUN npm install --ignore-scripts && npm run build || (echo "vite build failed, keeping static vanilla" && mkdir -p dist && cp -r index.html app.js style.css src dist/ 2>/dev/null || true)

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY miniapp ./miniapp
# overlay built assets if vite succeeded (dist contains hashed index)
COPY --from=frontend /build/miniapp/dist ./miniapp/dist
RUN mkdir -p /app/media/supplier && \
    if [ -f /app/miniapp/dist/index.html ]; then echo "using vite dist"; cp -r /app/miniapp/dist/* /app/miniapp/ 2>/dev/null || true; fi
EXPOSE 8000
CMD sh -c "python -m app.init_db && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} & python -m app.bot_shop & python -m app.bot_admin & python -m app.supplier_daemon & wait"
