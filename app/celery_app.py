from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from app.core.settings import get_settings

settings = get_settings()

import_exchange = Exchange("imports", type="direct")
dlx_exchange = Exchange("imports.dlx", type="direct")

celery_app = Celery(
    "shipment_imports",
    broker=settings.rabbitmq_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_default_queue="imports.dispatch",
    task_default_exchange=import_exchange.name,
    task_default_exchange_type=import_exchange.type,
    task_default_routing_key="imports.dispatch",
    task_queues=(
        Queue(
            "imports.dispatch",
            exchange=import_exchange,
            routing_key="imports.dispatch",
            queue_arguments={
                "x-dead-letter-exchange": dlx_exchange.name,
                "x-dead-letter-routing-key": "imports.dispatch.dlq",
            },
        ),
        Queue(
            "imports.dispatch.dlq",
            exchange=dlx_exchange,
            routing_key="imports.dispatch.dlq",
        ),
    ),
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    task_acks_late=settings.celery_task_acks_late,
    task_reject_on_worker_lost=settings.celery_task_reject_on_worker_lost,
)
