"""
ProspectGrid Flask API
Wraps existing geocoder, streetview, and scorer modules
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import uuid
import csv
import io
import logging
from datetime import datetime, timedelta
import json
import base64
import hmac
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import stripe
from sqlalchemy import select
from redis import Redis
from rq import Queue
import resend

from src.models import RawAddress, ScoredProperty, ProcessingStatus
from src.geocoder import Geocoder
from src.streetview import StreetViewFetcher
from src.gemini_scorer import GeminiPropertyScorer
from src.storage_helper import (
    save_session,
    load_session,
    cleanup_expired_sessions,
)
from src.db import SessionLocal, init_db
from src.db_models import Campaign, Property

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Allow frontend to call API

# Initialize DB schema
init_db()

# Cleanup expired sessions on startup
cleanup_expired_sessions()

# Initialize processors
geocoder = Geocoder()
streetview_fetcher = StreetViewFetcher()
property_scorer = GeminiPropertyScorer()

# Configure Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Redis queue
_redis_url = os.getenv("REDIS_URL")
_redis_conn = Redis.from_url(_redis_url) if _redis_url else None
queue = Queue("default", connection=_redis_conn) if _redis_conn else None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_results_token(campaign_id: str, expires_days: int = 7) -> str:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise ValueError("SECRET_KEY not configured")

    exp = int((datetime.utcnow() + timedelta(days=expires_days)).timestamp())
    payload = {"campaign_id": campaign_id, "exp": exp}
    payload_b64 = _b64url_encode(json.dumps(payload).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{payload_b64}.{sig_b64}"


def verify_results_token(token: str) -> str:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise ValueError("SECRET_KEY not configured")

    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        raise ValueError("Invalid token format")

    expected_sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_sig), sig_b64):
        raise ValueError("Invalid token signature")

    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    exp = int(payload.get("exp", 0))
    if datetime.utcnow().timestamp() > exp:
        raise ValueError("Token expired")

    campaign_id = payload.get("campaign_id")
    if not campaign_id:
        raise ValueError("Invalid token payload")

    return campaign_id


def send_results_email(email: str, campaign_id: str) -> None:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not configured; skipping email send")
        return

    if not email:
        logger.error("Missing email; skipping email send")
        return

    resend.api_key = api_key
    token = sign_results_token(campaign_id, expires_days=7)
    results_link = f"https://www.prospect-grid.com/results/{campaign_id}?token={token}"
    try:
        resend.Emails.send({
            "from": "ProspectGrid <results@prospect-grid.com>",
            "to": [email],
            "subject": "Your ProspectGrid Results Are Ready",
            "text": f"Your results are ready: {results_link}",
        })
        logger.info(f"Sent results email for campaign {campaign_id} to {email}")
    except Exception as e:
        logger.error(f"Failed to send results email: {e}", exc_info=True)


def _load_campaign_payload(campaign_id: str) -> dict | None:
    db = SessionLocal()
    try:
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except Exception:
            return None
        campaign = db.get(Campaign, campaign_uuid)
        if not campaign:
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
            parsed.append((payload.get("input_index", 0), prop, payload))

        parsed.sort(key=lambda x: x[0])
        properties = []
        for _, prop, payload in parsed:
            result = payload.get("result")
            if result is not None:
                properties.append(result)
            else:
                properties.append(
                    {
                        "input_address": prop.address,
                        "status": prop.status,
                        "error_message": prop.error,
                    }
                )

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
            "progress_percent": round((processed / total) * 100, 1) if total else 0.0,
            "properties": properties,
        }
    finally:
        db.close()


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/api/upload", methods=["POST"])
def upload_csv():
    """
    Upload and validate CSV file
    Returns: session_id, address_count, and validation results
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return (
                jsonify(
                    {
                        "error": "Service temporarily unavailable for maintenance. Please check back soon."
                    }
                ),
                503,
            )

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file.filename.endswith(".csv"):
            return jsonify({"error": "File must be a CSV"}), 400

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.DictReader(stream)

        addresses = []
        errors = []

        for idx, row in enumerate(csv_reader):
            try:
                row_lower = {k.lower().strip(): v for k, v in row.items()}
                if "street" not in row_lower:
                    errors.append(f"Row {idx + 1}: Missing 'street' column")
                    continue

                raw_address = RawAddress(
                    address=row_lower["street"],
                    city=row_lower.get("city"),
                    state=row_lower.get("state"),
                    zip=row_lower.get("zip"),
                )
                addresses.append(raw_address.model_dump())
            except Exception as e:
                errors.append(f"Row {idx + 1}: {str(e)}")

        if not addresses:
            return jsonify({"error": "No valid addresses found", "details": errors}), 400
        if len(addresses) > 500:
            return jsonify({"error": "Maximum 500 addresses per upload"}), 400

        session_id = str(uuid.uuid4())
        session_data = {
            "addresses": addresses,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
        }
        save_session(session_id, session_data)

        return (
            jsonify(
                {
                    "session_id": session_id,
                    "address_count": len(addresses),
                    "errors": errors if errors else None,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/estimate/<session_id>", methods=["GET"])
def get_estimate(session_id: str):
    """
    Get cost estimate for a session.
    Only full_scoring_standard is supported. Legacy tiers have been deprecated.
    """
    try:
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        address_count = len(session["addresses"])

        # Cost calculation for full_scoring_standard only
        geocoding_cost = address_count * 0.005
        streetview_cost = address_count * 0.007
        gemini_cost_per_image = 0.000075
        scoring_cost = address_count * gemini_cost_per_image

        full_scoring_standard_total = geocoding_cost + streetview_cost + scoring_cost

        return (
            jsonify(
                {
                    "address_count": address_count,
                    "costs": {
                        "full_scoring_standard": {
                            "subtotal": round(full_scoring_standard_total, 2),
                            "price": round(full_scoring_standard_total * 1.5, 2),
                            "description": "AI scoring (1 angle scored with Gemini)",
                        }
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Estimate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session():
    """
    Create a Stripe checkout session.
    Only full_scoring_standard is supported. Legacy tiers have been deprecated.
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return (
                jsonify(
                    {
                        "error": "Service temporarily unavailable for maintenance. Please check back soon."
                    }
                ),
                503,
            )

        data = request.json or {}
        upload_session_id = data.get("session_id")
        service_level = data.get("service_level")
        email = data.get("email")

        if not upload_session_id or not service_level:
            return jsonify({"error": "Missing required fields"}), 400

        # Enforce single tier
        if service_level != "full_scoring_standard":
            return (
                jsonify(
                    {
                        "error": "Service level no longer supported. Please purchase Full AI Scoring Standard."
                    }
                ),
                400,
            )

        session = load_session(upload_session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        address_count = len(session["addresses"])

        # Cost calculation for full_scoring_standard only
        geocoding_cost = address_count * 0.005
        streetview_cost = address_count * 0.007
        gemini_cost_per_image = 0.000075
        scoring_cost = address_count * gemini_cost_per_image

        total = geocoding_cost + streetview_cost + scoring_cost
        final_price = total * 1.5
        amount_cents = max(int(final_price * 100), 50)

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": "ProspectGrid - Full AI Scoring Standard",
                            "description": f"AI property analysis for {address_count} properties",
                        },
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url="https://www.prospect-grid.com/processing/{CHECKOUT_SESSION_ID}",
            cancel_url=f"https://www.prospect-grid.com/estimate/{upload_session_id}",
            customer_email=email,
            metadata={
                "upload_session_id": upload_session_id,
                "service_level": "full_scoring_standard",
                "address_count": address_count,
            },
        )

        return (
            jsonify({"checkout_url": checkout_session.url, "session_id": checkout_session.id}),
            200,
        )

    except Exception as e:
        logger.error(f"Checkout session creation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-payment/<stripe_session_id>", methods=["POST"])
def verify_payment(stripe_session_id: str):
    """
    Verify Stripe payment and start processing.
    Only full_scoring_standard is supported. Legacy tiers have been deprecated.
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return (
                jsonify(
                    {
                        "error": "Service temporarily unavailable for maintenance. Please check back soon."
                    }
                ),
                503,
            )

        db = SessionLocal()
        existing = (
            db.execute(select(Campaign).where(Campaign.stripe_session_id == stripe_session_id))
            .scalars()
            .first()
        )
        if existing:
            payload = _load_campaign_payload(str(existing.id))
            total = payload.get("total_properties", 0) if payload else 0
            return (
                jsonify(
                    {
                        "campaign_id": str(existing.id),
                        "status": existing.status,
                        "estimated_time_minutes": total / 20 if total else 0,
                    }
                ),
                200,
            )

        checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
        if checkout_session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 400

        upload_session_id = checkout_session.metadata.get("upload_session_id")
        service_level = checkout_session.metadata.get("service_level")

        # Enforce single tier
        if service_level != "full_scoring_standard":
            return (
                jsonify(
                    {
                        "error": "Service level no longer supported. Please purchase Full AI Scoring Standard."
                    }
                ),
                400,
            )

        session = load_session(upload_session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        # Standard tier only: single angle, scoring enabled
        street_view_mode = "standard"

        # Use verified email from Stripe (fallback to customer_details)
        email = checkout_session.customer_email or (
            (checkout_session.customer_details or {}).get("email")
            if hasattr(checkout_session, "customer_details")
            else None
        )

        campaign_id = str(uuid.uuid4())
        campaign = Campaign(
            id=uuid.UUID(campaign_id),
            stripe_session_id=stripe_session_id,
            email=email or "",
            status="processing",
            progress_percent=0,
        )
        db.add(campaign)
        db.flush()

        for idx, raw_addr_dict in enumerate(session["addresses"]):
            raw_addr = RawAddress(**raw_addr_dict)
            payload = {
                "input_index": idx,
                "raw_address": raw_addr_dict,
                "result": None,
            }
            db.add(
                Property(
                    campaign_id=campaign.id,
                    address=raw_addr.full_address,
                    score=None,
                    status="pending",
                    error=None,
                    data=json.dumps(payload),
                )
            )

        db.commit()

        if not queue:
            return jsonify({"error": "Queue unavailable"}), 500

        queue.enqueue(process_campaign, campaign_id, job_timeout=7200)
        logger.info(f"Enqueued background processing job for campaign {campaign_id}")

        return (
            jsonify(
                {
                    "campaign_id": campaign_id,
                    "status": "processing",
                    "estimated_time_minutes": len(session["addresses"]) / 20,
                }
            ),
            200,
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}", exc_info=True)
        return jsonify({"error": "Payment verification failed"}), 400
    except Exception as e:
        logger.error(f"Payment verification error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.route("/api/process/<session_id>", methods=["POST"])
def start_processing(session_id: str):
    """
    Start processing a session (requires verified Stripe payment)
    Returns: campaign_id
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return (
                jsonify(
                    {
                        "error": "Service temporarily unavailable for maintenance. Please check back soon."
                    }
                ),
                503,
            )

        data = request.json or {}
        stripe_session_id = data.get("stripe_session_id")

        if not stripe_session_id:
            return jsonify({"error": "Missing required field: stripe_session_id"}), 400

        db = SessionLocal()
        existing = (
            db.execute(select(Campaign).where(Campaign.stripe_session_id == stripe_session_id))
            .scalars()
            .first()
        )
        if existing:
            payload = _load_campaign_payload(str(existing.id))
            total = payload.get("total_properties", 0) if payload else 0
            return (
                jsonify(
                    {
                        "campaign_id": str(existing.id),
                        "status": existing.status,
                        "estimated_time_minutes": total / 20 if total else 0,
                    }
                ),
                200,
            )

        # Retrieve and validate Stripe checkout session
        try:
            checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error retrieving session: {e}", exc_info=True)
            return jsonify({"error": "Invalid Stripe session"}), 400

        # Validate payment completed
        if checkout_session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 400

        # Validate session ID matches
        if checkout_session.metadata.get("upload_session_id") != session_id:
            return jsonify({"error": "Session mismatch"}), 400

        # Validate service level
        service_level = checkout_session.metadata.get("service_level")
        if service_level != "full_scoring_standard":
            return (
                jsonify(
                    {
                        "error": "Service level no longer supported. Please purchase Full AI Scoring Standard."
                    }
                ),
                400,
            )

        # Load upload session
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        # Use verified email from Stripe (ignore client-provided email)
        email = checkout_session.customer_email or (
            (checkout_session.customer_details or {}).get("email")
            if hasattr(checkout_session, "customer_details")
            else None
        )

        # Standard tier only: single angle, scoring enabled
        street_view_mode = "standard"

        campaign_id = str(uuid.uuid4())
        campaign = Campaign(
            id=uuid.UUID(campaign_id),
            stripe_session_id=stripe_session_id,
            email=email or "",
            status="processing",
            progress_percent=0,
        )
        db.add(campaign)
        db.flush()

        for idx, raw_addr_dict in enumerate(session["addresses"]):
            raw_addr = RawAddress(**raw_addr_dict)
            payload = {
                "input_index": idx,
                "raw_address": raw_addr_dict,
                "result": None,
            }
            db.add(
                Property(
                    campaign_id=campaign.id,
                    address=raw_addr.full_address,
                    score=None,
                    status="pending",
                    error=None,
                    data=json.dumps(payload),
                )
            )

        db.commit()

        if not queue:
            return jsonify({"error": "Queue unavailable"}), 500

        queue.enqueue(process_campaign, campaign_id, job_timeout=7200)
        logger.info(f"Enqueued background processing job for campaign {campaign_id}")

        return (
            jsonify(
                {
                    "campaign_id": campaign_id,
                    "status": "processing",
                    "estimated_time_minutes": len(session["addresses"]) / 20,
                }
            ),
            200,
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}", exc_info=True)
        return jsonify({"error": "Payment verification failed"}), 400
    except Exception as e:
        logger.error(f"Processing start error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            db.close()
        except Exception:
            pass


def _score_placeholder(reason: str = "scoring_failed") -> dict:
    """
    Placeholder that prevents frontend crashes when it expects component fields.
    Keep keys aligned with what your frontend tries to read.
    """
    return {
        "error": reason,
        "roof": None,
        "siding": None,
        "landscape": None,
        "vacancy": None,
        "overall_score": None,
        "confidence": None,
        "reasoning": None,
    }


PROCESSING_WORKERS = int(os.getenv("PROCESSING_WORKERS", "3"))


def _process_single_property(campaign_id, raw_addr_dict, input_index):
    """
    Process one property through the full pipeline (geocode → street view → score).
    Thread-safe: does not touch the DB. Returns a result dict for the caller.
    """
    try:
        raw_addr = RawAddress(**raw_addr_dict)

        # Step 1: Geocode
        geocoded = geocoder.geocode(raw_addr)
        if not geocoded:
            return {
                "status": "failed",
                "error": "Geocoding failed",
                "score": None,
                "data": {
                    "input_index": input_index,
                    "raw_address": raw_addr_dict,
                    "result": {
                        "input_address": raw_addr.full_address,
                        "status": "failed",
                        "error_message": "Geocoding failed",
                    },
                },
            }

        prop = ScoredProperty.from_geocoded(geocoded, campaign_id)

        # Step 2: Street View (single angle)
        street_view = streetview_fetcher.fetch(geocoded, multi_angle=False)
        if street_view:
            prop.add_street_view(street_view)
        else:
            prop.processing_status = ProcessingStatus.NO_IMAGERY

        # Step 3: AI scoring
        if street_view and street_view.image_available:
            score = property_scorer.score(street_view)
            if score:
                prop.add_score(score)

        dumped = prop.model_dump(mode="json")
        has_score = (
            dumped.get("property_score") is not None
            or dumped.get("prospect_score") is not None
        )

        return {
            "status": "completed" if has_score else "failed",
            "error": None if has_score else "Scoring failed",
            "score": dumped.get("property_score") or dumped.get("prospect_score"),
            "data": {
                "input_index": input_index,
                "raw_address": raw_addr_dict,
                "result": dumped,
            },
        }

    except Exception as e:
        logger.error(f"Error processing property: {e}", exc_info=True)
        input_address = (
            raw_addr_dict.get("address")
            if isinstance(raw_addr_dict, dict)
            else str(raw_addr_dict)
        )
        return {
            "status": "failed",
            "error": str(e),
            "score": None,
            "data": {
                "input_index": input_index,
                "raw_address": raw_addr_dict,
                "result": {
                    "input_address": input_address,
                    "status": "failed",
                    "error_message": str(e),
                },
            },
        }


def process_campaign(campaign_id: str):
    """
    Process all addresses in a campaign using full_scoring_standard tier.
    Uses ThreadPoolExecutor for parallel processing — multiple properties
    geocode/fetch in parallel while Gemini calls are globally rate-limited via Redis.
    """
    db = SessionLocal()
    try:
        campaign_uuid = uuid.UUID(campaign_id)
        campaign = db.get(Campaign, campaign_uuid)
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        if campaign.status == "completed":
            return

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
            parsed.append((payload.get("input_index", 0), prop, payload))

        parsed.sort(key=lambda x: x[0])
        total = len(parsed)
        processed = len([p for p in props if p.status in ("completed", "failed")])

        # Collect pending work
        pending = []
        for _, prop_row, payload in parsed:
            if prop_row.status in ("completed", "failed"):
                continue
            raw_addr_dict = payload.get("raw_address") or {}
            input_index = payload.get("input_index", 0)
            pending.append((prop_row, raw_addr_dict, input_index))

        logger.info(
            f"Campaign {campaign_id}: {len(pending)} properties to process "
            f"with {PROCESSING_WORKERS} workers"
        )

        # Process properties in parallel
        with ThreadPoolExecutor(max_workers=PROCESSING_WORKERS) as executor:
            futures = {}
            for prop_row, raw_addr_dict, input_index in pending:
                future = executor.submit(
                    _process_single_property,
                    campaign_id,
                    raw_addr_dict,
                    input_index,
                )
                futures[future] = prop_row

            for future in as_completed(futures):
                prop_row = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    logger.error(f"Unexpected worker error: {e}", exc_info=True)
                    result = {
                        "status": "failed",
                        "error": str(e),
                        "score": None,
                        "data": {
                            "input_index": 0,
                            "raw_address": {},
                            "result": {
                                "input_address": "unknown",
                                "status": "failed",
                                "error_message": str(e),
                            },
                        },
                    }

                prop_row.status = result["status"]
                prop_row.error = result.get("error")
                prop_row.score = result.get("score")
                prop_row.data = json.dumps(result["data"])

                processed += 1
                campaign.progress_percent = (
                    round((processed / total) * 100, 1) if total else 0
                )
                db.commit()

        campaign.status = "completed"
        campaign.completed_at = datetime.utcnow()
        campaign.progress_percent = 100
        db.commit()

        send_results_email(campaign.email, str(campaign.id))
    finally:
        db.close()


@app.route("/api/status/<campaign_id>", methods=["GET"])
def get_status(campaign_id: str):
    try:
        campaign = _load_campaign_payload(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        return (
            jsonify(
                {
                    "campaign_id": campaign_id,
                    "status": campaign["status"],
                    "total_properties": campaign.get("total_properties") or 0,
                    "processed_count": campaign.get("processed_count") or 0,
                    "success_count": campaign.get("success_count", 0),
                    "failed_count": campaign.get("failed_count", 0),
                    "progress_percent": campaign.get("progress_percent", 0),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Status check error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/<campaign_id>", methods=["GET"])
def get_results(campaign_id: str):
    try:
        campaign = _load_campaign_payload(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        return (
            jsonify(
                {
                    "campaign_id": campaign_id,
                    "status": campaign["status"],
                    "total_properties": campaign["total_properties"],
                    "success_count": campaign.get("success_count", 0),
                    "failed_count": campaign.get("failed_count", 0),
                    "properties": campaign.get("properties", []),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Results fetch error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/<campaign_id>", methods=["GET"])
def export_csv(campaign_id: str):
    """Export campaign results as a CSV file with structured address columns."""
    try:
        campaign = _load_campaign_payload(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Address", "City", "State", "ZIP",
            "Score", "Confidence", "Status",
            "Street View URL", "Reasoning",
        ])

        for prop in campaign.get("properties", []):
            address = prop.get("address_street") or prop.get("input_address", "")
            writer.writerow([
                address,
                prop.get("city", ""),
                prop.get("state", ""),
                prop.get("zip", ""),
                prop.get("property_score") or prop.get("prospect_score", ""),
                prop.get("confidence_level") or prop.get("confidence", ""),
                prop.get("processing_status") or prop.get("status", ""),
                prop.get("streetview_url", ""),
                prop.get("score_reasoning") or prop.get("reasoning", ""),
            ])

        csv_data = output.getvalue()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=prospect-grid-{campaign_id[:8]}.csv"
            },
        )

    except Exception as e:
        logger.error(f"CSV export error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/validate-results-token", methods=["GET"])
def validate_results_token():
    try:
        token = request.args.get("token")
        if not token:
            return jsonify({"error": "Missing token"}), 400
        campaign_id = verify_results_token(token)
        return jsonify({"campaign_id": campaign_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/property/<campaign_id>/<int:property_index>", methods=["GET"])
def get_property(campaign_id: str, property_index: int):
    try:
        campaign = _load_campaign_payload(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        props = campaign.get("properties", [])
        if property_index >= len(props):
            return jsonify({"error": "Property not found"}), 404

        return jsonify(props[property_index]), 200

    except Exception as e:
        logger.error(f"Property fetch error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
