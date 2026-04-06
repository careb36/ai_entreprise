"""
NATS messaging client for Fabrique Enterprise Backend.

Wraps the ``nats`` async library with a simplified interface for
publish / subscribe / request-reply patterns used throughout the platform.

NATS subjects in use:
- ``task.assign``          — new backlog item assigned to an agent.
- ``task.update``          — backlog item metadata changed.
- ``task.progress``        — status transition on a work package.
- ``workspace.generate``   — VS Code Daemon: write generated files.
- ``workspace.execute``    — VS Code Daemon: run shell commands.
- ``glassroom.<sprint_id>``— real-time event feed consumed by the SSE endpoint.
- ``audit.write``          — append an entry to the audit log.
- ``system.maintenance.*`` — internal ops events (e.g. error ticket creation).
"""

import asyncio
import json
from typing import Optional, Callable, Dict, Any

import nats

from logging_config import setup_logger

logger = setup_logger("fabrique-nats")


class NATSClient:
    """
    Async NATS connection wrapper for publish/subscribe operations.

    Lifecycle:
    1. Instantiate with the target NATS server URL.
    2. Await ``connect()`` before calling any other method.
    3. Await ``close()`` during application shutdown to drain subscriptions.

    Attributes:
        nats_url: NATS server connection string (e.g. ``nats://localhost:4222``).
        nc: Underlying ``nats.NATS`` connection; ``None`` before ``connect()``.
        subscriptions: Map of subject pattern → asyncio Task for active listeners.
    """

    def __init__(self, nats_url: str = "nats://localhost:4222"):
        self.nats_url = nats_url
        self.nc: Optional[nats.NATS] = None
        # Tracks running listener tasks so they can be cancelled on shutdown.
        self.subscriptions: Dict[str, asyncio.Task] = {}

    async def connect(self):
        """
        Establish a connection to the NATS server.

        Raises:
            Exception: Propagated from the ``nats`` library if the server is
                       unreachable or authentication fails.
        """
        try:
            self.nc = await nats.connect(self.nats_url)
            logger.info(f"Connected to NATS at {self.nats_url}")
        except Exception as e:
            logger.error(f"Failed to connect to NATS: {e}")
            raise

    async def publish(self, subject: str, payload: Dict[str, Any]):
        """
        Publish a JSON-serialisable payload to a NATS subject.

        Args:
            subject: NATS subject string (e.g. ``"task.assign"``).
            payload: Dictionary that will be JSON-encoded before sending.

        Raises:
            RuntimeError: If called before ``connect()``.
        """
        if not self.nc:
            raise RuntimeError("NATS not connected")

        data = json.dumps(payload).encode()
        await self.nc.publish(subject, data)
        logger.info(f"Published to {subject}: {payload}")

    async def subscribe(self, subject: str, callback: Callable, queue: Optional[str] = None):
        """
        Subscribe to a subject pattern and dispatch messages to *callback*.

        Each subscription spawns a background ``asyncio.Task`` stored in
        ``self.subscriptions``.  Queue group support is provided via the
        optional *queue* parameter (load-balancing across multiple workers).

        Args:
            subject: NATS subject or wildcard (e.g. ``"glassroom.>"``).
            callback: Async callable invoked for each received message.
            queue:    Optional NATS queue group name for competing consumers.

        Raises:
            RuntimeError: If called before ``connect()``.
        """
        if not self.nc:
            raise RuntimeError("NATS not connected")

        async def wrapped_callback(msg):
            # Decode and validate JSON before forwarding to the caller's handler.
            try:
                data = json.loads(msg.data.decode())
                await callback(msg)
            except Exception as e:
                logger.error(f"Error in callback: {e}")

        sub = await self.nc.subscribe(subject, queue=queue)
        task = asyncio.create_task(self._listen(sub, callback))
        self.subscriptions[subject] = task
        logger.info(f"Subscribed to {subject}")

    async def _listen(self, sub, callback: Callable):
        """
        Internal coroutine that continuously reads messages from *sub* and
        forwards them to *callback*.

        Messages are acknowledged after the callback returns when using
        JetStream.  For core NATS the ack is silently skipped.  Exceptions
        in the callback are caught and logged so that a single bad message
        does not terminate the listener loop.
        """
        async for msg in sub.messages:
            try:
                data = json.loads(msg.data.decode())
                await callback(msg, data)
                # JetStream messages support ack(); core NATS messages do not.
                if hasattr(msg, '_ackd') or hasattr(msg, 'metadata'):
                    try:
                        await msg.ack()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def request(self, subject: str, payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """
        Send a request and block until a reply is received (request-reply pattern).

        Args:
            subject: Target NATS subject.
            payload: JSON-serialisable request body.
            timeout: Seconds to wait for a reply before raising ``TimeoutError``.

        Returns:
            Decoded JSON response dictionary.

        Raises:
            RuntimeError: If called before ``connect()``.
            nats.errors.TimeoutError: If no reply arrives within *timeout*.
        """
        if not self.nc:
            raise RuntimeError("NATS not connected")

        data = json.dumps(payload).encode()
        msg = await self.nc.request(subject, data, timeout=timeout)
        return json.loads(msg.data.decode())

    async def close(self):
        """
        Gracefully shut down all subscriptions and the NATS connection.

        Cancels every listener task before draining the connection, ensuring
        that in-flight messages are not silently dropped.
        """
        for task in self.subscriptions.values():
            task.cancel()
        if self.nc:
            await self.nc.close()
        logger.info("NATS connection closed")
