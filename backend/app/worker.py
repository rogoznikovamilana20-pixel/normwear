import asyncio
from celery import Celery
from .config import settings

celery_app = Celery("normwear", broker=settings.redis_url, backend=settings.redis_url)

@celery_app.task

def health_task():
    return "ok"

@celery_app.task

def supplier_import(days: int = 14):
    from .supplier import SupplierWorker
    return asyncio.run(SupplierWorker().import_products(days=days))
