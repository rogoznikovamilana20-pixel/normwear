FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# miniapp is copied in root Dockerfile context; for backend-only builds keep placeholder
RUN mkdir -p /app/miniapp /app/media/supplier
