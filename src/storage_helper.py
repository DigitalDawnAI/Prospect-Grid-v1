"""
File-based session storage to persist across Railway deployments
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Use /tmp on Railway - it's ephemeral but works for short-term storage
# For production, should use PostgreSQL or Redis
# Single Railway instance means /tmp will work for same-request access
STORAGE_DIR = Path(os.getenv('STORAGE_DIR', '/tmp/prospectgrid_sessions'))


def _ensure_storage_dir():
    """Create storage directory if it doesn't exist"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    """
    Save session data to disk

    Args:
        session_id: Unique session identifier
        data: Session data dictionary
    """
    try:
        _ensure_storage_dir()
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved session {session_id} to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save session {session_id}: {e}")
        raise


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Load session data from disk

    Args:
        session_id: Unique session identifier

    Returns:
        Session data dict or None if not found
    """
    try:
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        if not file_path.exists():
            logger.warning(f"Session {session_id} not found at {file_path}")
            return None

        with open(file_path, 'r') as f:
            data = json.load(f)

        # Check if expired
        if 'expires_at' in data:
            expires_at = datetime.fromisoformat(data['expires_at'])
            if datetime.now() > expires_at:
                logger.info(f"Session {session_id} expired, deleting")
                file_path.unlink()
                return None

        logger.info(f"Loaded session {session_id} from {file_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load session {session_id}: {e}")
        return None


def delete_session(session_id: str) -> None:
    """
    Delete session data from disk

    Args:
        session_id: Unique session identifier
    """
    try:
        file_path = STORAGE_DIR / f"session_{session_id}.json"
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted session {session_id}")
    except Exception as e:
        logger.error(f"Failed to delete session {session_id}: {e}")


def save_campaign(campaign_id: str, data: Dict[str, Any]) -> None:
    """
    Save campaign data to disk

    Args:
        campaign_id: Unique campaign identifier
        data: Campaign data dictionary
    """
    try:
        _ensure_storage_dir()
        file_path = STORAGE_DIR / f"campaign_{campaign_id}.json"
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved campaign {campaign_id} to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save campaign {campaign_id}: {e}")
        raise


def load_campaign(campaign_id: str) -> Optional[Dict[str, Any]]:
    """
    Load campaign data from disk

    Args:
        campaign_id: Unique campaign identifier

    Returns:
        Campaign data dict or None if not found
    """
    try:
        file_path = STORAGE_DIR / f"campaign_{campaign_id}.json"

        # Debug: List all files in storage directory
        if STORAGE_DIR.exists():
            all_files = list(STORAGE_DIR.glob("*.json"))
            logger.info(f"Storage dir has {len(all_files)} files: {[f.name for f in all_files[:5]]}")
        else:
            logger.warning(f"Storage directory {STORAGE_DIR} does not exist!")

        if not file_path.exists():
            logger.warning(f"Campaign {campaign_id} not found at {file_path}")
            logger.warning(f"Looking for: campaign_{campaign_id}.json")
            return None

        with open(file_path, 'r') as f:
            data = json.load(f)

        logger.info(f"Loaded campaign {campaign_id} from {file_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load campaign {campaign_id}: {e}", exc_info=True)
        return None


def cleanup_expired_sessions() -> None:
    """
    Delete expired session files
    Run this periodically to clean up old data
    """
    try:
        if not STORAGE_DIR.exists():
            return

        count = 0
        for file_path in STORAGE_DIR.glob("session_*.json"):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)

                if 'expires_at' in data:
                    expires_at = datetime.fromisoformat(data['expires_at'])
                    if datetime.now() > expires_at:
                        file_path.unlink()
                        count += 1
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")

        if count > 0:
            logger.info(f"Cleaned up {count} expired sessions")
    except Exception as e:
        logger.error(f"Failed to cleanup expired sessions: {e}")
