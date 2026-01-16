"""
ProspectGrid Flask API
Wraps existing geocoder, streetview, and scorer modules
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid
import csv
import io
import logging
from datetime import datetime, timedelta
import os
import stripe
import threading

from src.models import RawAddress, ScoredProperty, ProcessingStatus
from src.geocoder import Geocoder
from src.streetview import StreetViewFetcher
from src.gemini_scorer import GeminiPropertyScorer
from src.storage_helper import (
    save_session,
    load_session,
    save_campaign,
    load_campaign,
    cleanup_expired_sessions,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Allow frontend to call API

# Cleanup expired sessions on startup
cleanup_expired_sessions()

# Initialize processors
geocoder = Geocoder()
streetview_fetcher = StreetViewFetcher()
property_scorer = GeminiPropertyScorer()

# Configure Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


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
            return jsonify({"error": "Service temporarily unavailable for maintenance. Please check back soon."}), 503

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

        return jsonify(
            {
                "session_id": session_id,
                "address_count": len(addresses),
                "errors": errors if errors else None,
            }
        ), 200

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/estimate/<session_id>", methods=["GET"])
def get_estimate(session_id: str):
    """
    Get cost estimate for a session
    Returns: cost breakdown
    """
    try:
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        address_count = len(session["addresses"])

        geocoding_cost = address_count * 0.005
        streetview_cost_standard = address_count * 0.007
        streetview_cost_premium = address_count * 0.028

        gemini_cost_per_image = 0.000075
        scoring_cost_standard = address_count * gemini_cost_per_image
        scoring_cost_premium = address_count * (gemini_cost_per_image * 4)

        streetview_standard_total = geocoding_cost + streetview_cost_standard
        streetview_premium_total = geocoding_cost + streetview_cost_premium

        full_scoring_standard_total = streetview_standard_total + scoring_cost_standard
        full_scoring_premium_total = streetview_premium_total + scoring_cost_premium

        return jsonify(
            {
                "address_count": address_count,
                "costs": {
                    "streetview_standard": {
                        "subtotal": round(streetview_standard_total, 2),
                        "price": round(streetview_standard_total * 1.5, 2),
                        "description": "1 optimized angle",
                    },
                    "streetview_premium": {
                        "subtotal": round(streetview_premium_total, 2),
                        "price": round(streetview_premium_total * 1.5, 2),
                        "description": "4 angles (N, E, S, W)",
                    },
                    "full_scoring_standard": {
                        "subtotal": round(full_scoring_standard_total, 2),
                        "price": round(full_scoring_standard_total * 1.5, 2),
                        "description": "AI scoring (1 angle scored with Gemini)",
                    },
                    "full_scoring_premium": {
                        "subtotal": round(full_scoring_premium_total, 2),
                        "price": round(full_scoring_premium_total * 1.5, 2),
                        "description": "AI scoring (4 angles scored with Gemini)",
                    },
                },
            }
        ), 200

    except Exception as e:
        logger.error(f"Estimate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session():
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return jsonify({"error": "Service temporarily unavailable for maintenance. Please check back soon."}), 503

        data = request.json or {}
        upload_session_id = data.get("session_id")
        service_level = data.get("service_level")
        email = data.get("email")

        if not upload_session_id or not service_level:
            return jsonify({"error": "Missing required fields"}), 400

        session = load_session(upload_session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        address_count = len(session["addresses"])

        geocoding_cost = address_count * 0.005
        streetview_cost_standard = address_count * 0.007
        streetview_cost_premium = address_count * 0.028

        gemini_cost_per_image = 0.000075
        scoring_cost_standard = address_count * gemini_cost_per_image
        scoring_cost_premium = address_count * (gemini_cost_per_image * 4)

        if service_level != "full_scoring_standard":
            return jsonify({"error": "Service level no longer supported. Please purchase Full AI Scoring Standard."}), 400

        total = geocoding_cost + streetview_cost_standard + scoring_cost_standard


        total = geocoding_cost + streetview_cost_standard + scoring_cost_standard

        final_price = total * 1.5
        amount_cents = max(int(final_price * 100), 50)

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"ProspectGrid - {service_level.replace('_', ' ').title()}",
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
                "service_level": service_level,
                "address_count": address_count,
            },
        )

        return jsonify({"checkout_url": checkout_session.url, "session_id": checkout_session.id}), 200

    except Exception as e:
        logger.error(f"Checkout session creation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-payment/<stripe_session_id>", methods=["POST"])
def verify_payment(stripe_session_id: str):
    """
    Verify Stripe payment and start processing
    Returns: campaign_id
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return jsonify({"error": "Service temporarily unavailable for maintenance. Please check back soon."}), 503

        checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
        if checkout_session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 400

        upload_session_id = checkout_session.metadata["upload_session_id"]
        service_level = checkout_session.metadata["service_level"]

        # Enforce single tier
        if service_level != "full_scoring_standard":
            return jsonify(
                {"error": "Service level no longer supported. Please purchase Full AI Scoring Standard."}
            ), 400


        session = load_session(upload_session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        # Standard tier only: single angle, scoring enabled
        street_view_mode = "standard"

        campaign_id = str(uuid.uuid4())
        campaign_data = {
            "campaign_id": campaign_id,
            "session_id": upload_session_id,
            "email": checkout_session.customer_email,
            "service_level": service_level,
            "street_view_mode": street_view_mode,
            "payment_intent_id": checkout_session.payment_intent,
            "stripe_session_id": stripe_session_id,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "total_properties": len(session["addresses"]),
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "properties": [],
        }
        save_campaign(campaign_id, campaign_data)

        thread = threading.Thread(target=process_campaign, args=(campaign_id,), daemon=True)
        thread.start()
        logger.info(f"Started background processing thread for campaign {campaign_id}")

        return jsonify(
            {
                "campaign_id": campaign_id,
                "status": "processing",
                "estimated_time_minutes": len(session["addresses"]) / 20,
            }
        ), 200

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}", exc_info=True)
        return jsonify({"error": "Payment verification failed"}), 400
    except Exception as e:
        logger.error(f"Payment verification error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/process/<session_id>", methods=["POST"])
def start_processing(session_id: str):
    """
    Start processing a session (requires verified Stripe payment)

    Returns: campaign_id
    """
    try:
        if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
            return jsonify({"error": "Service temporarily unavailable for maintenance. Please check back soon."}), 503

        data = request.json or {}
        stripe_session_id = data.get("stripe_session_id")

        if not stripe_session_id:
        if not stripe_session_id:
            return jsonify({"error": "Missing required field: stripe_session_id"}), 400

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
            return jsonify({"error": "Service level no longer supported. Please purchase Full AI Scoring Standard."}), 400

        # Load upload session
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        # Load upload session
        session = load_session(session_id)
        if not session:
            return jsonify({"error": "Session not found or expired"}), 404

        # Use verified email from Stripe (ignore client-provided email)
        email = checkout_session.customer_email

        # Standard tier only: single angle, scoring enabled
        street_view_mode = "standard"

        street_view_mode = "standard"

        campaign_id = str(uuid.uuid4())
        campaign_data = {
            "campaign_id": campaign_id,
            "session_id": session_id,
            "email": checkout_session.customer_email,
            "service_level": service_level,
            "street_view_mode": street_view_mode,
            "payment_intent_id": checkout_session.payment_intent,
            "stripe_session_id": stripe_session_id,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "total_properties": len(session["addresses"]),
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "properties": [],
        }
        save_campaign(campaign_id, campaign_data)

        thread = threading.Thread(target=process_campaign, args=(campaign_id,), daemon=True)
        thread.start()
        logger.info(f"Started background processing thread for campaign {campaign_id}")

        return jsonify(
            {
                "campaign_id": campaign_id,
                "status": "processing",
                "estimated_time_minutes": len(session["addresses"]) / 20,
            }
        ), 200

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}", exc_info=True)
        return jsonify({"error": "Payment verification failed"}), 400
    except Exception as e:
        logger.error(f"Processing start error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


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


def process_campaign(campaign_id: str):
    """
    Process all addresses in a campaign.
    NOTE: /tmp storage is ephemeral and threads are not durable on Railway;
    this is best moved to a real worker + persistent DB.
    """
    campaign = load_campaign(campaign_id)
    if not campaign:
        logger.error(f"Campaign {campaign_id} not found")
        return

    session = load_session(campaign["session_id"])
    if not session:
        logger.error(f"Session {campaign['session_id']} not found")
        return

    service_level = campaign["service_level"]
    street_view_mode = campaign.get("street_view_mode", "standard")

    multi_angle = street_view_mode == "premium"
    needs_scoring = "full_scoring" in service_level

    for raw_addr_dict in session["addresses"]:
        try:
            raw_addr = RawAddress(**raw_addr_dict)

            # Step 1: Geocode
            geocoded = geocoder.geocode(raw_addr)
            if not geocoded:
                campaign["failed_count"] += 1
                campaign["processed_count"] += 1
                campaign["properties"].append(
                    {
                        "input_address": raw_addr.full_address,
                        "status": "failed",
                        "error_message": "Geocoding failed",
                    }
                )
                save_campaign(campaign_id, campaign)
                continue

            prop = ScoredProperty.from_geocoded(geocoded, campaign_id)

            # Step 2: Street View
            street_view = streetview_fetcher.fetch(geocoded, multi_angle=multi_angle)
            if street_view:
                prop.add_street_view(street_view)
            else:
                prop.processing_status = ProcessingStatus.NO_IMAGERY

            # Step 3: VLM scoring
            if needs_scoring and street_view and street_view.image_available:
                if multi_angle and getattr(street_view, "image_urls_multi_angle", None):
                    raw_scores = property_scorer.score_multiple(street_view, street_view.image_urls_multi_angle)

                    # Prevent null entries (frontend crash). Preserve slots with placeholders.
                    safe_scores = []
                    for s in (raw_scores or []):
                        safe_scores.append(s if s is not None else _score_placeholder("scoring_failed"))

                    # If all failed, still attach placeholders so UI can render safely
                    if safe_scores:
                        prop.add_scores_multi_angle(safe_scores)

                else:
                    score = property_scorer.score(street_view)
                    if score:
                        prop.add_score(score)

            # Persist property
            dumped = prop.model_dump()

            campaign["properties"].append(dumped)
            campaign["processed_count"] += 1

            # Count success/failure more safely than ProcessingStatus.COMPLETE
            # (avoids "scored=1" when no score fields exist)
            has_any_score = False
            if dumped.get("score") is not None:
                has_any_score = True
            if dumped.get("scores_multi_angle"):
                # treat any non-null as success (placeholders still count as "not really scored")
                # If you want placeholders to count as failure, tighten this condition.
                has_any_score = any(x and not x.get("error") for x in dumped["scores_multi_angle"])

            if needs_scoring:
                if has_any_score:
                    campaign["success_count"] += 1
                else:
                    campaign["failed_count"] += 1
            else:
                # Non-scoring tiers: consider success if imagery exists
                if street_view and street_view.image_available:
                    campaign["success_count"] += 1
                else:
                    campaign["failed_count"] += 1

            save_campaign(campaign_id, campaign)

        except Exception as e:
            logger.error(f"Error processing address: {e}", exc_info=True)
            campaign["failed_count"] += 1
            campaign["processed_count"] += 1
            campaign["properties"].append(
                {
                    "input_address": raw_addr_dict.get("address") if isinstance(raw_addr_dict, dict) else str(raw_addr_dict),
                    "status": "failed",
                    "error_message": str(e),
                }
            )
            save_campaign(campaign_id, campaign)
            continue

    campaign["status"] = "completed"
    campaign["completed_at"] = datetime.now().isoformat()
    save_campaign(campaign_id, campaign)


@app.route("/api/status/<campaign_id>", methods=["GET"])
def get_status(campaign_id: str):
    try:
        campaign = load_campaign(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        total = campaign.get("total_properties") or 0
        processed = campaign.get("processed_count") or 0
        progress = round((processed / total) * 100, 1) if total else 0.0

        return jsonify(
            {
                "campaign_id": campaign_id,
                "status": campaign["status"],
                "total_properties": total,
                "processed_count": processed,
                "success_count": campaign.get("success_count", 0),
                "failed_count": campaign.get("failed_count", 0),
                "progress_percent": progress,
            }
        ), 200

    except Exception as e:
        logger.error(f"Status check error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/<campaign_id>", methods=["GET"])
def get_results(campaign_id: str):
    try:
        campaign = load_campaign(campaign_id)
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        return jsonify(
            {
                "campaign_id": campaign_id,
                "status": campaign["status"],
                "total_properties": campaign["total_properties"],
                "success_count": campaign.get("success_count", 0),
                "failed_count": campaign.get("failed_count", 0),
                "properties": campaign.get("properties", []),
            }
        ), 200

    except Exception as e:
        logger.error(f"Results fetch error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/property/<campaign_id>/<int:property_index>", methods=["GET"])
def get_property(campaign_id: str, property_index: int):
    try:
        campaign = load_campaign(campaign_id)
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
