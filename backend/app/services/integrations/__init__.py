"""The integrations context: telling the outside world (ADR 0012, 0016).

The company's notification webhook (``webhooks``: a ``before_flush``
listener, installed once by ``main.py``, that queues a delivery in the same
transaction as the change of a task, a sprint or a verification, and signs
and posts it) and the outbox processor that publishes queued events to NATS
and posts the webhook deliveries (``outbox_service``). It listens to changes
of the task record and the evidence; nothing below it imports it. It imports
only ``contracts``, ``platform``, ``tasks`` and ``evidence`` among the
contexts; ``scripts/check_architecture.py`` keeps it so.
"""
