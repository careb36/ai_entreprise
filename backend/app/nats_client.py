import asyncio
import json
import logging
from typing import Optional, Callable, Dict, Any

import nats


logger = logging.getLogger(__name__)


class NATSClient:
    """NATS connection wrapper for publish/subscribe operations."""
    
    def __init__(self, nats_url: str = "nats://localhost:4222"):
        self.nats_url = nats_url
        self.nc: Optional[nats.NATS] = None
        self.subscriptions: Dict[str, asyncio.Task] = {}
    
    async def connect(self):
        """Connect to NATS server."""
        try:
            self.nc = await nats.connect(self.nats_url)
            logger.info(f"Connected to NATS at {self.nats_url}")
        except Exception as e:
            logger.error(f"Failed to connect to NATS: {e}")
            raise
    
    async def publish(self, subject: str, payload: Dict[str, Any]):
        """Publish message to subject."""
        if not self.nc:
            raise RuntimeError("NATS not connected")
        
        data = json.dumps(payload).encode()
        await self.nc.publish(subject, data)
        logger.info(f"Published to {subject}: {payload}")
    
    async def subscribe(self, subject: str, callback: Callable, queue: Optional[str] = None):
        """Subscribe to subject with callback."""
        if not self.nc:
            raise RuntimeError("NATS not connected")
        
        async def wrapped_callback(msg):
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
        """Listen to subscription."""
        async for msg in sub.messages:
            try:
                data = json.loads(msg.data.decode())
                await callback(msg, data)
                await msg.ack()
            except Exception as e:
                logger.error(f"Error processing message: {e}")
    
    async def request(self, subject: str, payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """Send request and wait for response."""
        if not self.nc:
            raise RuntimeError("NATS not connected")
        
        data = json.dumps(payload).encode()
        msg = await self.nc.request(subject, data, timeout=timeout)
        return json.loads(msg.data.decode())
    
    async def close(self):
        """Close connection and cancel subscriptions."""
        for task in self.subscriptions.values():
            task.cancel()
        if self.nc:
            await self.nc.close()
        logger.info("NATS connection closed")
