import pytest
from unittest.mock import MagicMock, patch
from src.backend.cache.graph_cache import GraphCacheManager

@pytest.fixture
def mock_redis():
    with patch('src.backend.cache.graph_cache.redis.from_url') as mock:
        mock_client = MagicMock()
        mock.return_value = mock_client
        yield mock_client

def test_cache_miss_returns_none(mock_redis):
    """Ensure a cache miss gracefully returns None without failing."""
    mock_redis.get.return_value = None
    cache = GraphCacheManager()
    
    result = cache.get_cached_query("MATCH (n) RETURN n", {"limit": 10})
    
    assert result is None
    mock_redis.get.assert_called_once()

def test_cache_hit_returns_data(mock_redis):
    """Ensure a cache hit successfully deserializes and returns the payload."""
    mock_redis.get.return_value = '{"data": "test_payload"}'
    cache = GraphCacheManager()
    
    result = cache.get_cached_query("MATCH (n) RETURN n", {"limit": 10})
    
    assert result == {"data": "test_payload"}

def test_set_cached_query_stores_tags(mock_redis):
    """Ensure caching a query also sets up the tag-based invalidation sets."""
    cache = GraphCacheManager()
    
    cache.set_cached_query(
        query="MATCH (n) RETURN n", 
        params={"limit": 10}, 
        result={"data": "test_payload"}, 
        tags=["node:123"]
    )
    
    # Verify the result was cached with TTL
    mock_redis.setex.assert_called_once()
    
    # Verify the tag was added to the eviction set
    mock_redis.sadd.assert_called_once()
    mock_redis.expire.assert_called_once()

def test_invalidate_by_tags_deletes_keys(mock_redis):
    """Ensure active invalidation properly cleans up both query keys and tag sets."""
    mock_redis.smembers.return_value = {"graph_cache:query:hash123"}
    cache = GraphCacheManager()
    
    cache.invalidate_by_tags(["node:123"])
    
    # Should delete the query key and the tag key
    mock_redis.delete.assert_called_once()
    deleted_keys = mock_redis.delete.call_args[0]
    
    assert "graph_cache:query:hash123" in deleted_keys
    assert "graph_cache:tag:node:123" in deleted_keys
