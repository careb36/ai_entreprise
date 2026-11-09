import json
import hashlib
import os
from typing import Any, Dict, List, Optional
import redis

# STD-OPS-1: Configured by environment, safe default fallback
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

class GraphCacheManager:
    """
    Manages caching for Neo4j graph queries to ensure sub-400ms latency.
    Adheres to STD-ARCH-2: Single responsibility (caching) and explicit interface.
    """
    def __init__(self):
        # STD-SEC-1: No secrets in code, utilizing environment configuration
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        self.default_ttl = 900  # 15 minutes for semi-static lineage data

    def _generate_key(self, query: str, params: Dict[str, Any]) -> str:
        """Generates a deterministic cache key based on query and parameters."""
        query_hash = hashlib.sha256(f"{query}:{json.dumps(params, sort_keys=True)}".encode()).hexdigest()
        return f"graph_cache:query:{query_hash}"

    def get_cached_query(self, query: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retrieves a cached graph query result."""
        key = self._generate_key(query, params)
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    def set_cached_query(self, query: str, params: Dict[str, Any], result: Dict[str, Any], tags: List[str], ttl: Optional[int] = None) -> None:
        """
        Caches a query result and associates it with entity tags for targeted active invalidation.
        """
        key = self._generate_key(query, params)
        active_ttl = ttl or self.default_ttl
        
        # Store the serialized query result
        self.redis_client.setex(key, active_ttl, json.dumps(result))
        
        # Store tag associations for targeted invalidation
        for tag in tags:
            tag_key = f"graph_cache:tag:{tag}"
            self.redis_client.sadd(tag_key, key)
            self.redis_client.expire(tag_key, active_ttl)

    def invalidate_by_tags(self, tags: List[str]) -> None:
        """
        Invalidates all cached queries associated with the given tags.
        Must be called during graph mutations to ensure data consistency.
        """
        keys_to_delete = set()
        for tag in tags:
            tag_key = f"graph_cache:tag:{tag}"
            associated_keys = self.redis_client.smembers(tag_key)
            keys_to_delete.update(associated_keys)
            keys_to_delete.add(tag_key)
            
        if keys_to_delete:
            self.redis_client.delete(*keys_to_delete)
