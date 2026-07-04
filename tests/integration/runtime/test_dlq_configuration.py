from __future__ import annotations

from app.celery_app import celery_app


def test_dispatch_queue_has_the_configured_rabbitmq_dead_letter_route() -> None:
    queues = {queue.name: queue for queue in celery_app.conf.task_queues}

    dispatch_queue = queues["imports.dispatch"]
    dead_letter_queue = queues["imports.dispatch.dlq"]

    assert dispatch_queue.durable is True
    assert dispatch_queue.queue_arguments == {
        "x-queue-type": "quorum",
        "x-dead-letter-exchange": "imports.dlx",
        "x-dead-letter-routing-key": "imports.dispatch.dlq",
    }
    assert dead_letter_queue.durable is True
    assert dead_letter_queue.exchange.name == "imports.dlx"
    assert dead_letter_queue.routing_key == "imports.dispatch.dlq"
    assert dead_letter_queue.queue_arguments == {"x-queue-type": "quorum"}
