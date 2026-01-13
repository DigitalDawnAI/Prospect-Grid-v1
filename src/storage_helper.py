"""
Session and campaign storage with Redis persistence.

Uses Redis as primary storage for durability across Railway deployments.
Falls back to /tmp file storage if Redis is unavailable.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, date
import logging

from .cache import get_cache, Cache

logger = logging.getLogger(__name__)

# Fallback to /tmp when Redis unavailable
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "/tmp/prospectgrid_sessions"))


def _ensure_storage_dir() -> None:
    """Create storage directory if it doesn't exist"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _json_default(o: Any) -> Any:
    """
    JSON serializer for objects not serializable by default json code.
    Converts datetime/date to ISO-8601 strings.
    """
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    """
    Save session data to Redis (primary) or disk (fallback).

    Args:
        session_id: Unique session identifier
        data: Session data dictionary
    """
    cache = get_cache()

    # Try Redis first
    if cache.enabled:
        try:
            key = Cache.session_key(session_id)
            # Serialize with custom default for datetime
            serialized = json.loads(json.dumps(data, default=_json_default))
            if cache.set(key, serialized, ttl_seconds=Cache.TTL_SESSION):
                logger.info(f"Saved session {session_id} to Redis")
                return
        except Exception as e:
            logger.warning(f"Redis save failed for session {session_id}, falling back to file: {e}")

    # Fallback to file storage
    try:
        _ensure_storage_dir()
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2, default=_json_default)
        logger.info(f"Saved session {session_id} to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save session {session_id}: {e}", exc_info=True)
        raise


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Load session data from Redis (primary) or disk (fallback).

    Args:
        session_id: Unique session identifier

    Returns:
        Session data dict or None if not found
    """
    cache = get_cache()

    # Try Redis first
    if cache.enabled:
        try:
            key = Cache.session_key(session_id)
            data = cache.get(key)
            if data is not None:
                logger.info(f"Loaded session {session_id} from Redis")
                return data
        except Exception as e:
            logger.warning(f"Redis load failed for session {session_id}: {e}")

    # Fallback to file storage
    try:
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        if not file_path.exists():
            logger.warning(f"Session {session_id} not found")
            return None

        with open(file_path, "r") as f:
            data = json.load(f)

        # Check if expired
        if "expires_at" in data:
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires_at:
                logger.info(f"Session {session_id} expired, deleting")
                file_path.unlink()
                return None

        logger.info(f"Loaded session {session_id} from {file_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load session {session_id}: {e}", exc_info=True)
        return None


def delete_session(session_id: str) -> None:
    """
    Delete session data from Redis and disk.

    Args:
        session_id: Unique session identifier
    """
    cache = get_cache()

    # Delete from Redis
    if cache.enabled:
        try:
            key = Cache.session_key(session_id)
            cache.delete(key)
            logger.info(f"Deleted session {session_id} from Redis")
        except Exception as e:
            logger.warning(f"Redis delete failed for session {session_id}: {e}")

    # Also delete from file (cleanup)
    try:
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted session {session_id} from disk")
    except Exception as e:
        logger.error(f"Failed to delete session {session_id}: {e}", exc_info=True)


def save_campaign(campaign_id: str, data: Dict[str, Any]) -> None:
    """
    Save campaign data to Redis (primary) or disk (fallback).

    Args:
        campaign_id: Unique campaign identifier
        data: Campaign data dictionary
    """
    cache = get_cache()

    # Try Redis first
    if cache.enabled:
        try:
            key = Cache.campaign_key(campaign_id)
            # Serialize with custom default for datetime
            serialized = json.loads(json.dumps(data, default=_json_default))
            if cache.set(key, serialized, ttl_seconds=Cache.TTL_CAMPAIGN):
                logger.info(f"Saved campaign {campaign_id} to Redis")
                return
        except Exception as e:
            logger.warning(f"Redis save failed for campaign {campaign_id}, falling back to file: {e}")

    # Fallback to file storage
    try:
        _ensure_storage_dir()
        file_path = STORAGE_DIR / f"campaign_{campaign_id}.json"
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2, default=_json_default)
        logger.info(f"Saved campaign {campaign_id} to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save campaign {campaign_id}: {e}", exc_info=True)
        raise


def load_campaign(campaign_id: str) -> Optional[Dict[str, Any]]:
    """
    Load campaign data from Redis (primary) or disk (fallback).

    Args:
        campaign_id: Unique campaign identifier

    Returns:
        Campaign data dict or None if not found
    """
    cache = get_cache()

    # Try Redis first
    if cache.enabled:
        try:
            key = Cache.campaign_key(campaign_id)
            data = cache.get(key)
            if data is not None:
                logger.info(f"Loaded campaign {campaign_id} from Redis")
                return data
        except Exception as e:
            logger.warning(f"Redis load failed for campaign {campaign_id}: {e}")

    # Fallback to file storage
    try:
        file_path = STORAGE_DIR / f"campaign_{campaign_id}.json"

        # Debug: List all files in storage directory
        if STORAGE_DIR.exists():
            all_files = list(STORAGE_DIR.glob("*.json"))
            logger.info(
                f"Storage dir has {len(all_files)} files: {[f.name for f in all_files[:5]]}"
            )
        else:
            logger.warning(f"Storage directory {STORAGE_DIR} does not exist!")

        if not file_path.exists():
            logger.warning(f"Campaign {campaign_id} not found at {file_path}")
            logger.warning(f"Looking for: campaign_{campaign_id}.json")
            return None

        with open(file_path, "r") as f:
            data = json.load(f)

        logger.info(f"Loaded campaign {campaign_id} from {file_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load campaign {campaign_id}: {e}", exc_info=True)
        return None


def cleanup_expired_sessions() -> None:
    """
    Delete expired session files from disk.
    Run this periodically to clean up old data.

    Note: Redis handles expiration automatically via TTL,
    this only cleans up file-based fallback storage.
    """
    try:
        if not STORAGE_DIR.exists():
            return

        count = 0
        for file_path in STORAGE_DIR.glob("session_*.json"):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)

                if "expires_at" in data:
                    expires_at = datetime.fromisoformat(data["expires_at"])
                    if datetime.now() > expires_at:
                        file_path.unlink()
                        count += 1
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}", exc_info=True)

        if count > 0:
            logger.info(f"Cleaned up {count} expired sessions")
    except Exception as e:
        logger.error(f"Failed to cleanup expired sessions: {e}", exc_info=True)
