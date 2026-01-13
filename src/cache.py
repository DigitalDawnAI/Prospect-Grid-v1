"""
Redis-based caching layer for API responses.

Provides caching for:
- Geocoding results (30-day TTL)
- Street View coverage/no-coverage (7-day TTL for negative, 30-day for positive)

Falls back gracefully when Redis is unavailable.
"""

import os
import json
import hashlib
import logging
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# Try to import redis, but don't fail if unavailable
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


class Cache:
    """
    Redis cache wrapper with graceful fallback.

    When Redis is unavailable (no REDIS_URL or import fails),
    all operations become no-ops and return cache misses.
    """

    # TTL constants (in seconds)
    TTL_GEOCODE = 86400 * 30      # 30 days for geocode results
    TTL_COVERAGE = 86400 * 30     # 30 days for positive coverage
    TTL_NO_COVERAGE = 86400 * 7   # 7 days for negative coverage (Street View updates quarterly)
    TTL_SESSION = 86400           # 24 hours for upload sessions
    TTL_CAMPAIGN = 86400 * 7      # 7 days for campaign results

    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize cache with optional Redis URL.

        Args:
            redis_url: Redis connection URL. If None, reads from REDIS_URL env var.
        """
        self._client = None
        self._enabled = False

        url = redis_url or os.getenv("REDIS_URL")

        if not REDIS_AVAILABLE:
            logger.info("Redis not installed - caching disabled")
            return

        if not url:
            logger.info("REDIS_URL not set - caching disabled")
            return

        try:
            self._client = redis.from_url(url, decode_responses=True)
            # Test connection
            self._client.ping()
            self._enabled = True
            logger.info("Redis cache connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed, caching disabled: {e}")
            self._client = None

    @property
    def enabled(self) -> bool:
        """Check if caching is enabled."""
        return self._enabled and self._client is not None

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value (deserialized from JSON) or None if not found
        """
        if not self.enabled:
            return None

        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Cache get failed for {key}: {e}")
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = TTL_GEOCODE) -> bool:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache (must be JSON-serializable)
            ttl_seconds: Time-to-live in seconds

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            serialized = json.dumps(value)
            self._client.setex(key, ttl_seconds, serialized)
            return True
        except Exception as e:
            logger.warning(f"Cache set failed for {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if deleted, False otherwise
        """
        if not self.enabled:
            return False

        try:
            self._client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Cache delete failed for {key}: {e}")
            return False

    # --- Key generators ---

    @staticmethod
    def geocode_key(address: str) -> str:
        """
        Generate cache key for geocoding results.

        Normalizes address (lowercase, strip whitespace) before hashing
        to ensure consistent cache hits for equivalent addresses.

        Args:
            address: Full address string

        Returns:
            Cache key in format "geo:{md5_hash}"
        """
        normalized = address.lower().strip()
        # Remove extra whitespace
        normalized = " ".join(normalized.split())
        hash_val = hashlib.md5(normalized.encode()).hexdigest()
        return f"geo:{hash_val}"

    @staticmethod
    def coverage_key(lat: float, lng: float) -> str:
        """
        Generate cache key for Street View coverage.

        Rounds coordinates to 5 decimal places (~1.1m precision)
        which is sufficient for Street View lookups.

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            Cache key in format "sv:{lat5}:{lng5}"
        """
        lat_rounded = round(lat, 5)
        lng_rounded = round(lng, 5)
        return f"sv:{lat_rounded}:{lng_rounded}"

    @staticmethod
    def session_key(session_id: str) -> str:
        """
        Generate cache key for upload sessions.

        Args:
            session_id: Unique session identifier (UUID)

        Returns:
            Cache key in format "session:{session_id}"
        """
        return f"session:{session_id}"

    @staticmethod
    def campaign_key(campaign_id: str) -> str:
        """
        Generate cache key for campaign data.

        Args:
            campaign_id: Unique campaign identifier (UUID)

        Returns:
            Cache key in format "campaign:{campaign_id}"
        """
        return f"campaign:{campaign_id}"


# Singleton instance for easy access
_cache_instance: Optional[Cache] = None


def get_cache() -> Cache:
    """
    Get the singleton cache instance.

    Creates the instance on first call.
    """
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = Cache()
    return _cache_instance
