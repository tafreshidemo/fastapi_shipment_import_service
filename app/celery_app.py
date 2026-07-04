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
    include=[
        "app.workers.tasks",
        "app.workers.startup_recovery",
        "app.workers.beat_schedule",
    ],
)
celery_app.conf.update(
    task_default_queue="imports.dispatch",
    task_default_queue_type="quorum",
    task_default_exchange=import_exchange.name,
    task_default_exchange_type=import_exchange.type,
    task_default_routing_key="imports.dispatch",
    task_queues=(
        Queue(
            "imports.dispatch",
            exchange=import_exchange,
            routing_key="imports.dispatch",
            durable=True,
            queue_arguments={
                "x-queue-type": "quorum",
                "x-dead-letter-exchange": dlx_exchange.name,
                "x-dead-letter-routing-key": "imports.dispatch.dlq",
            },
        ),
        Queue(
            "imports.dispatch.dlq",
            exchange=dlx_exchange,
            routing_key="imports.dispatch.dlq",
            durable=True,
            queue_arguments={"x-queue-type": "quorum"},
        ),
    ),
    broker_transport_options={"confirm_publish": True},
    control_queue_exclusive=True,
    worker_detect_quorum_queues=True,
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    task_acks_late=settings.celery_task_acks_late,
    task_reject_on_worker_lost=settings.celery_task_reject_on_worker_lost,
    beat_schedule={
        "recover-stale-imports": {
            "task": "imports.recover_stale_imports",
            "schedule": settings.watchdog_interval_seconds,
        },
    },
)
