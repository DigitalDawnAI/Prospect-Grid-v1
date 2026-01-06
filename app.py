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
from typing import List, Dict, Any
import os
import stripe
import threading

from src.models import RawAddress, ScoredProperty, ProcessingStatus
from src.geocoder import Geocoder
from src.streetview import StreetViewFetcher
from src.gemini_scorer import GeminiPropertyScorer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Allow frontend to call API

# In-memory storage for MVP (replace with PostgreSQL later)
# Structure: {session_id: {addresses: [...], campaign_id: str, email: str}}
upload_sessions = {}
# Structure: {campaign_id: {properties: [...], status: str, ...}}
campaigns = {}

# Initialize processors
geocoder = Geocoder()
streetview_fetcher = StreetViewFetcher()
property_scorer = GeminiPropertyScorer()  # Using Gemini 2.0 Flash

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route('/api/upload', methods=['POST'])
def upload_csv():
    """
    Upload and validate CSV file

    Returns:
        session_id, address_count, and validation results
    """
    try:
        # Check maintenance mode
        if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
            return jsonify({
                "error": "Service temporarily unavailable for maintenance. Please check back soon."
            }), 503

        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        
        if not file.filename.endswith('.csv'):
            return jsonify({"error": "File must be a CSV"}), 400
        
        # Read CSV
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.DictReader(stream)
        
        # Parse addresses
        addresses = []
        errors = []
        
        for idx, row in enumerate(csv_reader):
            try:
                # Require 'street' column
                if 'street' in row:
                    raw_address = RawAddress(
                        address=row['street'],
                        city=row.get('city'),
                        state=row.get('state'),
                        zip=row.get('zip')
                    )
                else:
                    errors.append(f"Row {idx + 1}: Missing 'street' column")
                    continue
                
                addresses.append(raw_address.model_dump())
            except Exception as e:
                errors.append(f"Row {idx + 1}: {str(e)}")
        
        if not addresses:
            return jsonify({"error": "No valid addresses found", "details": errors}), 400
        
        if len(addresses) > 500:
            return jsonify({"error": "Maximum 500 addresses per upload"}), 400
        
        # Create session
        session_id = str(uuid.uuid4())
        upload_sessions[session_id] = {
            "addresses": addresses,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
        }
        
        return jsonify({
            "session_id": session_id,
            "address_count": len(addresses),
            "errors": errors if errors else None
        }), 200
    
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/estimate/<session_id>', methods=['GET'])
def get_estimate(session_id: str):
    """
    Get cost estimate for a session
    
    Args:
        session_id: Upload session ID
    
    Returns:
        Cost breakdown for Street View only vs Full Scoring
    """
    try:
        if session_id not in upload_sessions:
            return jsonify({"error": "Session not found or expired"}), 404
        
        session = upload_sessions[session_id]
        address_count = len(session['addresses'])
        
        # Cost calculation
        geocoding_cost = address_count * 0.005
        streetview_cost_standard = address_count * 0.007  # 1 image
        streetview_cost_premium = address_count * 0.028   # 4 images

        # Gemini 2.0 Flash scoring costs (99.7% cheaper than Claude!)
        gemini_cost_per_image = 0.000075
        scoring_cost_standard = address_count * gemini_cost_per_image  # 1 image
        scoring_cost_premium = address_count * (gemini_cost_per_image * 4)  # 4 images

        # Standard Street View (1 angle)
        streetview_standard_total = geocoding_cost + streetview_cost_standard

        # Premium Street View (4 angles)
        streetview_premium_total = geocoding_cost + streetview_cost_premium

        # Full AI Scoring (with standard street view - 1 image scored)
        full_scoring_standard_total = streetview_standard_total + scoring_cost_standard

        # Full AI Scoring (with premium street view - 4 images scored)
        full_scoring_premium_total = streetview_premium_total + scoring_cost_premium

        # Add 50% markup for revenue
        return jsonify({
            "address_count": address_count,
            "costs": {
                "streetview_standard": {
                    "subtotal": round(streetview_standard_total, 2),
                    "price": round(streetview_standard_total * 1.5, 2),
                    "description": "1 optimized angle"
                },
                "streetview_premium": {
                    "subtotal": round(streetview_premium_total, 2),
                    "price": round(streetview_premium_total * 1.5, 2),
                    "description": "4 angles (N, E, S, W)"
                },
                "full_scoring_standard": {
                    "subtotal": round(full_scoring_standard_total, 2),
                    "price": round(full_scoring_standard_total * 1.5, 2),
                    "description": "AI scoring (1 angle scored with Gemini 2.0 Flash)"
                },
                "full_scoring_premium": {
                    "subtotal": round(full_scoring_premium_total, 2),
                    "price": round(full_scoring_premium_total * 1.5, 2),
                    "description": "AI scoring (4 angles scored with Gemini 2.0 Flash)"
                }
            }
        }), 200
    
    except Exception as e:
        logger.error(f"Estimate error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    Create a Stripe checkout session for payment

    Request body:
        {
            "session_id": "uuid",
            "service_level": "streetview_standard" | "streetview_premium" | "full_scoring_standard" | "full_scoring_premium",
            "email": "user@example.com"
        }

    Returns:
        {
            "checkout_url": "https://checkout.stripe.com/...",
            "session_id": "cs_..."
        }
    """
    try:
        # Check maintenance mode
        if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
            return jsonify({
                "error": "Service temporarily unavailable for maintenance. Please check back soon."
            }), 503

        data = request.json
        upload_session_id = data.get('session_id')
        service_level = data.get('service_level')
        email = data.get('email')

        if not upload_session_id or not service_level:
            return jsonify({"error": "Missing required fields"}), 400

        if upload_session_id not in upload_sessions:
            return jsonify({"error": "Session not found or expired"}), 404

        session = upload_sessions[upload_session_id]
        address_count = len(session['addresses'])

        # Calculate pricing (same logic as /api/estimate)
        geocoding_cost = address_count * 0.005
        streetview_cost_standard = address_count * 0.007
        streetview_cost_premium = address_count * 0.028

        gemini_cost_per_image = 0.000075
        scoring_cost_standard = address_count * gemini_cost_per_image
        scoring_cost_premium = address_count * (gemini_cost_per_image * 4)

        # Calculate total based on service level
        if service_level == "streetview_standard":
            total = geocoding_cost + streetview_cost_standard
        elif service_level == "streetview_premium":
            total = geocoding_cost + streetview_cost_premium
        elif service_level == "full_scoring_standard":
            total = geocoding_cost + streetview_cost_standard + scoring_cost_standard
        elif service_level == "full_scoring_premium":
            total = geocoding_cost + streetview_cost_premium + scoring_cost_premium
        else:
            return jsonify({"error": "Invalid service level"}), 400

        # Apply 50% markup
        final_price = total * 1.5

        # Convert to cents for Stripe (minimum $0.50)
        amount_cents = max(int(final_price * 100), 50)

        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'ProspectGrid - {service_level.replace("_", " ").title()}',
                        'description': f'AI property analysis for {address_count} properties',
                    },
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'https://www.prospect-grid.com/processing/{{CHECKOUT_SESSION_ID}}',
            cancel_url=f'https://www.prospect-grid.com/estimate/{upload_session_id}',
            customer_email=email,
            metadata={
                'upload_session_id': upload_session_id,
                'service_level': service_level,
                'address_count': address_count
            }
        )

        return jsonify({
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id
        }), 200

    except Exception as e:
        logger.error(f"Checkout session creation error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/verify-payment/<stripe_session_id>', methods=['POST'])
def verify_payment(stripe_session_id: str):
    """
    Verify Stripe payment and start processing

    Args:
        stripe_session_id: Stripe checkout session ID

    Returns:
        campaign_id for tracking results
    """
    try:
        # Check maintenance mode
        if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
            return jsonify({
                "error": "Service temporarily unavailable for maintenance. Please check back soon."
            }), 503

        # Retrieve the Stripe session
        checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)

        # Verify payment was successful
        if checkout_session.payment_status != 'paid':
            return jsonify({"error": "Payment not completed"}), 400

        # Get metadata
        upload_session_id = checkout_session.metadata['upload_session_id']
        service_level = checkout_session.metadata['service_level']

        if upload_session_id not in upload_sessions:
            return jsonify({"error": "Session not found or expired"}), 404

        # Determine street view mode from service level
        street_view_mode = "premium" if "premium" in service_level else "standard"

        # Create campaign
        campaign_id = str(uuid.uuid4())
        session = upload_sessions[upload_session_id]

        campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "session_id": upload_session_id,
            "email": checkout_session.customer_email,
            "service_level": service_level,
            "street_view_mode": street_view_mode,
            "payment_intent_id": checkout_session.payment_intent,
            "stripe_session_id": stripe_session_id,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "total_properties": len(session['addresses']),
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "properties": []
        }

        # Start processing in background thread
        thread = threading.Thread(target=process_campaign, args=(campaign_id,), daemon=True)
        thread.start()
        logger.info(f"Started background processing for campaign {campaign_id}")

        return jsonify({
            "campaign_id": campaign_id,
            "status": "processing",
            "estimated_time_minutes": len(session['addresses']) / 20
        }), 200

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        return jsonify({"error": "Payment verification failed"}), 400
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/process/<session_id>', methods=['POST'])
def start_processing(session_id: str):
    """
    Start processing a session after payment

    Request body:
        {
            "service_level": "streetview_standard" | "streetview_premium" | "full_scoring_standard" | "full_scoring_premium",
            "email": "user@example.com",
            "payment_intent_id": "pi_xxx" (Stripe payment)
        }

    Returns:
        campaign_id for tracking results
    """
    try:
        # Check maintenance mode
        if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
            return jsonify({
                "error": "Service temporarily unavailable for maintenance. Please check back soon."
            }), 503

        if session_id not in upload_sessions:
            return jsonify({"error": "Session not found or expired"}), 404

        data = request.json
        service_level = data.get('service_level', 'full_scoring_standard')
        email = data.get('email')
        payment_intent_id = data.get('payment_intent_id')

        # Determine street view mode from service level
        street_view_mode = "premium" if "premium" in service_level else "standard"

        # Create campaign
        campaign_id = str(uuid.uuid4())
        session = upload_sessions[session_id]

        campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "session_id": session_id,
            "email": email,
            "service_level": service_level,
            "street_view_mode": street_view_mode,
            "payment_intent_id": payment_intent_id,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "total_properties": len(session['addresses']),
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "properties": []
        }
        
        # Start processing in background thread
        thread = threading.Thread(target=process_campaign, args=(campaign_id,), daemon=True)
        thread.start()
        logger.info(f"Started background processing for campaign {campaign_id}")

        return jsonify({
            "campaign_id": campaign_id,
            "status": "processing",
            "estimated_time_minutes": len(session['addresses']) / 20  # ~20 properties/minute
        }), 200
    
    except Exception as e:
        logger.error(f"Processing start error: {e}")
        return jsonify({"error": str(e)}), 500


def process_campaign(campaign_id: str):
    """
    Process all addresses in a campaign in background thread
    Handles geocoding, Street View fetching, and AI scoring
    """
    try:
        logger.info(f"Starting campaign processing: {campaign_id}")
        campaign = campaigns[campaign_id]
        session = upload_sessions[campaign['session_id']]
        service_level = campaign['service_level']
        street_view_mode = campaign.get('street_view_mode', 'standard')

        # Determine if we need multi-angle images
        multi_angle = (street_view_mode == 'premium')

        # Determine if we need AI scoring
        needs_scoring = 'full_scoring' in service_level

        for raw_addr_dict in session['addresses']:
            try:
                # Convert dict back to RawAddress
                raw_addr = RawAddress(**raw_addr_dict)

                # Step 1: Geocode
                geocoded = geocoder.geocode(raw_addr)
                if not geocoded:
                    campaign['failed_count'] += 1
                    campaign['properties'].append({
                        "input_address": raw_addr.full_address,
                        "status": "failed",
                        "error_message": "Geocoding failed"
                    })
                    campaign['processed_count'] += 1
                    continue

                # Create ScoredProperty
                prop = ScoredProperty.from_geocoded(geocoded, campaign_id)

                # Step 2: Get Street View
                street_view = streetview_fetcher.fetch(geocoded, multi_angle=multi_angle)
                if street_view:
                    prop.add_street_view(street_view)
                else:
                    prop.processing_status = ProcessingStatus.NO_IMAGERY

                # Step 3: Score with Gemini 2.0 Flash (all tiers now use Gemini)
                if needs_scoring and street_view and street_view.image_available:
                    # For premium tier with multi-angle, score all 4 angles
                    if multi_angle and street_view.image_urls_multi_angle:
                        scores = property_scorer.score_multiple(street_view, street_view.image_urls_multi_angle)
                        if scores and any(s is not None for s in scores):
                            prop.add_scores_multi_angle(scores)
                    else:
                        # For standard tier, score single image
                        score = property_scorer.score(street_view)
                        if score:
                            prop.add_score(score)

                campaign['properties'].append(prop.model_dump())
                campaign['processed_count'] += 1
                if prop.processing_status == ProcessingStatus.COMPLETE:
                    campaign['success_count'] += 1

            except Exception as e:
                logger.error(f"Error processing address {raw_addr.full_address}: {e}", exc_info=True)
                campaign['failed_count'] += 1
                campaign['processed_count'] += 1

        # Mark campaign as complete
        campaign['status'] = 'completed'
        campaign['completed_at'] = datetime.now().isoformat()
        logger.info(f"Campaign {campaign_id} completed: {campaign['success_count']}/{campaign['total_properties']} successful")

    except Exception as e:
        logger.error(f"Fatal error in campaign {campaign_id}: {e}", exc_info=True)
        # Mark campaign as failed
        if campaign_id in campaigns:
            campaigns[campaign_id]['status'] = 'failed'
            campaigns[campaign_id]['error'] = str(e)


@app.route('/api/status/<campaign_id>', methods=['GET'])
def get_status(campaign_id: str):
    """Get processing status for a campaign"""
    try:
        if campaign_id not in campaigns:
            return jsonify({"error": "Campaign not found"}), 404
        
        campaign = campaigns[campaign_id]
        
        return jsonify({
            "campaign_id": campaign_id,
            "status": campaign['status'],
            "total_properties": campaign['total_properties'],
            "processed_count": campaign['processed_count'],
            "success_count": campaign['success_count'],
            "failed_count": campaign['failed_count'],
            "progress_percent": round((campaign['processed_count'] / campaign['total_properties']) * 100, 1)
        }), 200
    
    except Exception as e:
        logger.error(f"Status check error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/results/<campaign_id>', methods=['GET'])
def get_results(campaign_id: str):
    """Get all scored properties for a campaign"""
    try:
        if campaign_id not in campaigns:
            return jsonify({"error": "Campaign not found"}), 404
        
        campaign = campaigns[campaign_id]
        
        return jsonify({
            "campaign_id": campaign_id,
            "status": campaign['status'],
            "total_properties": campaign['total_properties'],
            "success_count": campaign['success_count'],
            "failed_count": campaign['failed_count'],
            "properties": campaign['properties']
        }), 200
    
    except Exception as e:
        logger.error(f"Results fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/property/<campaign_id>/<int:property_index>', methods=['GET'])
def get_property(campaign_id: str, property_index: int):
    """Get single property details"""
    try:
        if campaign_id not in campaigns:
            return jsonify({"error": "Campaign not found"}), 404
        
        campaign = campaigns[campaign_id]
        
        if property_index >= len(campaign['properties']):
            return jsonify({"error": "Property not found"}), 404
        
        return jsonify(campaign['properties'][property_index]), 200
    
    except Exception as e:
        logger.error(f"Property fetch error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
