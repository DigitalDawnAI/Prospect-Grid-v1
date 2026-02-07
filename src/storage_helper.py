"""
File-based session storage to persist across Railway deployments
"""
import json
import os
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, date
import logging

from sqlalchemy import select

from src.db import SessionLocal
from src.db_models import Campaign, Property

logger = logging.getLogger(__name__)

# Use /tmp on Railway - it's ephemeral but works for short-term storage
# For production, should use PostgreSQL or Redis
# Single Railway instance means /tmp will work for same-request access
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
    Save session data to disk

    Args:
        session_id: Unique session identifier
        data: Session data dictionary
    """
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
        logger.error(f"Failed to delete session {session_id}: {e}", exc_info=True)


def save_campaign(campaign_id: str, data: Dict[str, Any]) -> None:
    """
    Save campaign data to PostgreSQL

    Args:
        campaign_id: Unique campaign identifier
        data: Campaign data dictionary
    """
    try:
        db = SessionLocal()
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except Exception:
            campaign_uuid = uuid.uuid4()

        campaign = db.get(Campaign, campaign_uuid)
        if not campaign:
            campaign = Campaign(
                id=campaign_uuid,
                stripe_session_id=data.get("stripe_session_id") or "",
                email=data.get("email") or "",
                status=data.get("status") or "processing",
                progress_percent=data.get("progress_percent") or 0,
                completed_at=data.get("completed_at"),
            )
            db.add(campaign)
            db.flush()
        else:
            if data.get("stripe_session_id"):
                campaign.stripe_session_id = data["stripe_session_id"]
            if data.get("email"):
                campaign.email = data["email"]
            if data.get("status"):
                campaign.status = data["status"]
            if data.get("progress_percent") is not None:
                campaign.progress_percent = data["progress_percent"]
            if data.get("completed_at"):
                campaign.completed_at = data["completed_at"]

        if "properties" in data:
            db.query(Property).filter(Property.campaign_id == campaign.id).delete()
            for idx, prop in enumerate(data.get("properties") or []):
                address = prop.get("input_address") or prop.get("address_full") or prop.get("address")
                score = prop.get("property_score") or prop.get("prospect_score")
                status = prop.get("status") or "completed"
                error = prop.get("error_message")
                payload = {
                    "input_index": idx,
                    "raw_address": None,
                    "result": prop,
                }
                db.add(
                    Property(
                        campaign_id=campaign.id,
                        address=address,
                        score=score,
                        status=status,
                        error=error,
                        data=json.dumps(payload),
                    )
                )

        db.commit()
        logger.info(f"Saved campaign {campaign_id} to PostgreSQL")
    except Exception as e:
        logger.error(f"Failed to save campaign {campaign_id}: {e}", exc_info=True)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


def load_campaign(campaign_id: str) -> Optional[Dict[str, Any]]:
    """
    Load campaign data from PostgreSQL

    Args:
        campaign_id: Unique campaign identifier

    Returns:
        Campaign data dict or None if not found
    """
    try:
        db = SessionLocal()
        campaign_uuid = uuid.UUID(campaign_id)
        campaign = db.get(Campaign, campaign_uuid)
        if not campaign:
            logger.warning(f"Campaign {campaign_id} not found in PostgreSQL")
            return None

        props = (
            db.execute(select(Property).where(Property.campaign_id == campaign.id))
            .scalars()
            .all()
        )
        parsed = []
        for prop in props:
            try:
                payload = json.loads(prop.data) if prop.data else {}
            except Exception:
                payload = {}
            parsed.append((payload.get("input_index", 0), payload))

        parsed.sort(key=lambda x: x[0])
        properties = []
        for _, payload in parsed:
            result = payload.get("result")
            if result is not None:
                properties.append(result)

        total = len(props)
        processed = len([p for p in props if p.status in ("completed", "failed")])
        success_count = len([p for p in props if p.status == "completed"])
        failed_count = len([p for p in props if p.status == "failed"])

        return {
            "campaign_id": str(campaign.id),
            "stripe_session_id": campaign.stripe_session_id,
            "email": campaign.email,
            "status": campaign.status,
            "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
            "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
            "total_properties": total,
            "processed_count": processed,
            "success_count": success_count,
            "failed_count": failed_count,
            "properties": properties,
        }
    except Exception as e:
        logger.error(f"Failed to load campaign {campaign_id}: {e}", exc_info=True)
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


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
