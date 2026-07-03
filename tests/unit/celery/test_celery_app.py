from __future__ import annotations

from app.celery_app import celery_app
from app.core.settings import get_settings


def test_step1_celery_queue_and_dlq_configuration() -> None:
    settings = get_settings()
    queues = {queue.name: queue for queue in celery_app.conf.task_queues}

    assert "imports.dispatch" in queues
    assert "imports.dispatch.dlq" in queues

    main_queue = queues["imports.dispatch"]
    dlq_queue = queues["imports.dispatch.dlq"]

    assert main_queue.exchange.name == "imports"
    assert main_queue.exchange.type == "direct"
    assert main_queue.routing_key == "imports.dispatch"
    assert main_queue.queue_arguments == {
        "x-dead-letter-exchange": "imports.dlx",
        "x-dead-letter-routing-key": "imports.dispatch.dlq",
    }

    assert dlq_queue.exchange.name == "imports.dlx"
    assert dlq_queue.exchange.type == "direct"
    assert dlq_queue.routing_key == "imports.dispatch.dlq"

    assert celery_app.conf.worker_prefetch_multiplier == settings.celery_worker_prefetch_multiplier
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert "app.workers.tasks.process_import" not in celery_app.tasks
