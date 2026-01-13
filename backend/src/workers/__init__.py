from celery import Celery
import os


celery_app = Celery(
    "orders",
    broker=os.getenv("REDIS_URI", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URI", "redis://localhost:6379/0"),
)

celery_app.conf.task_routes = {
    "backend.src.workers.ingest_worker.*": {"queue": "ingest"},
    "backend.src.workers.output_worker.*": {"queue": "exports"},
}

celery_app.conf.task_default_retry_delay = 10

