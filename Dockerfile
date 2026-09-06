FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# для бесплатного хостинга (Render, Railway) — порт приходит из env PORT
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY data ./data
# создаём папки которые нужны в runtime (БД, логи)
RUN mkdir -p /app/logs /app/data/yandex
# healthcheck для Render/Fly
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT','8000') + '/healthz', timeout=4)" || exit 1
EXPOSE 8000
CMD ["python", "-m", "app.main"]