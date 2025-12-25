# ProspectGrid Backend - Development Log

## Project Overview

ProspectGrid is a Flask-based REST API that processes real estate addresses through:
1. **Geocoding** (Google Maps API) - Convert addresses to coordinates
2. **Street View Fetching** (Google Street View API) - Get property images
3. **AI Scoring** (Anthropic Claude) - Score properties for investment potential

---

## Session: December 25, 2025 - Initial Setup & Deployment

### What We Accomplished

✅ **Environment Setup**
- Copied `.env.example` to `.env`
- Added API keys for Google Maps and Anthropic Claude
- Fixed Python 3.13 compatibility issue (upgraded pydantic from 2.5.0 to >=2.10.0)

✅ **Local Testing**
- Installed all dependencies via `pip install -r requirements.txt`
- Ran Flask app locally on port 5001 (port 5000 was in use by macOS AirPlay)
- Successfully tested all API endpoints:
  - Health check
  - CSV upload
  - Cost estimation

✅ **Git Repository**
- Initialized git repository
- Created proper `.gitignore` to exclude sensitive files (.env, __pycache__, .DS_Store)
- Pushed code to GitHub: https://github.com/DigitalDawnAI/Prospect-Grid-v1

✅ **Railway Deployment**
- Installed Railway CLI
- Deployed to Railway via GitHub integration
- Configured environment variables:
  - `GOOGLE_MAPS_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `FLASK_ENV=production`
  - `STRIPE_SECRET_KEY` (for future payment integration)
- Live URL: https://web-production-a42df.up.railway.app

✅ **Production Testing**
- Verified all endpoints working in production
- Tested CSV upload with 3 sample addresses
- Confirmed cost estimation calculations

---

## Project Structure

```
prospectgrid-backend/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Procfile              # Railway deployment config (gunicorn)
├── .env.example          # Environment template
├── .env                  # Local environment (git-ignored)
├── .gitignore           # Git exclusions
├── README.md            # API documentation
├── test_api.py          # API test script
└── src/
    ├── __init__.py
    ├── models.py        # Pydantic data models
    ├── geocoder.py      # Google Maps geocoding
    ├── streetview.py    # Street View image fetching
    └── scorer.py        # Claude AI property scoring
```

---

## API Endpoints

### Base URL
- **Local**: `http://localhost:5001`
- **Production**: `https://web-production-a42df.up.railway.app`

### Endpoints

#### 1. Health Check
```bash
GET /health
```
Response:
```json
{
  "status": "ok",
  "timestamp": "2025-12-25T06:00:01.312728"
}
```

#### 2. Upload CSV
```bash
POST /api/upload
Content-Type: multipart/form-data

# CSV format:
address,city,state,zip
123 Main St,Atlantic City,NJ,08401
```
Response:
```json
{
  "session_id": "550a5365-6706-462a-b900-c741583965a0",
  "address_count": 3,
  "errors": null
}
```

#### 3. Get Cost Estimate
```bash
GET /api/estimate/{session_id}
```
Response:
```json
{
  "address_count": 3,
  "costs": {
    "streetview_only": {
      "subtotal": 0.04,
      "price": 0.05
    },
    "full_scoring": {
      "subtotal": 0.11,
      "price": 0.17
    }
  }
}
```

#### 4. Start Processing
```bash
POST /api/process/{session_id}
Content-Type: application/json

{
  "service_level": "full_scoring",
  "email": "user@example.com",
  "payment_intent_id": "pi_xxx"
}
```

#### 5. Check Status
```bash
GET /api/status/{campaign_id}
```

#### 6. Get Results
```bash
GET /api/results/{campaign_id}
```

---

## Environment Variables

### Required
```bash
GOOGLE_MAPS_API_KEY=<your_google_maps_key>
ANTHROPIC_API_KEY=<your_anthropic_key>
FLASK_ENV=development|production
PORT=5001
```

### Optional (for future payment integration)
```bash
STRIPE_SECRET_KEY=<your_stripe_secret_key>
STRIPE_WEBHOOK_SECRET=<your_stripe_webhook_secret>
```

---

## Setup Instructions

### Local Development

1. **Clone the repository**
```bash
git clone https://github.com/DigitalDawnAI/Prospect-Grid-v1.git
cd Prospect-Grid-v1
```

2. **Set up environment**
```bash
cp .env.example .env
# Edit .env and add your API keys
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Run locally**
```bash
python3 app.py
```

5. **Test**
```bash
python3 test_api.py
```

### Deployment to Railway

1. **Push to GitHub**
```bash
git push origin main
```

2. **Deploy via Railway Dashboard**
   - Go to https://railway.app/new
   - Click "Deploy from GitHub repo"
   - Select `DigitalDawnAI/Prospect-Grid-v1`
   - Add environment variables in Settings → Variables
   - Deploy automatically runs via `Procfile`

3. **Verify deployment**
```bash
curl https://your-app.railway.app/health
```

---

## Known Issues & Fixes

### Issue 1: pydantic build failure on Python 3.13
**Problem**: `pydantic==2.5.0` doesn't have pre-built wheels for Python 3.13

**Solution**: Upgraded to `pydantic>=2.10.0` in requirements.txt

### Issue 2: Port 5000 already in use (macOS)
**Problem**: macOS AirPlay Receiver uses port 5000

**Solution**: Changed PORT to 5001 in .env

### Issue 3: Railway deployment - "Google Maps API key not found"
**Problem**: Environment variables were set as "Shared Variables" instead of service-level variables

**Solution**: Added variables directly to the "web" service in Railway dashboard

---

## Testing

### Local Testing
```bash
# Start the server
python3 app.py

# Run test suite
python3 test_api.py
```

### Production Testing
```bash
# Health check
curl https://web-production-a42df.up.railway.app/health

# Upload test CSV
curl -X POST -F "file=@test.csv" \
  https://web-production-a42df.up.railway.app/api/upload
```

---

## Cost Breakdown

### API Usage Costs (per address)
- **Geocoding**: $0.005 (Google Maps)
- **Street View**: $0.007 (Google Street View)
- **AI Scoring**: $0.025 (Anthropic Claude)

### Pricing (50% markup)
- **Street View Only**: $0.018/address
- **Full Scoring**: $0.056/address

---

## Tech Stack

- **Framework**: Flask 3.0.0
- **Web Server**: Gunicorn 21.2.0
- **APIs**:
  - Google Maps Geocoding API
  - Google Street View Static API
  - Anthropic Claude API
- **Data Validation**: Pydantic 2.10+
- **Deployment**: Railway
- **Repository**: GitHub

---

## Next Steps

### MVP Completion
- [ ] Add PostgreSQL database (replace in-memory storage)
- [ ] Implement background job processing (Celery/RQ)
- [ ] Add Stripe payment webhook handler
- [ ] Add email notifications (SendGrid)
- [ ] Implement CSV export endpoint

### Production Readiness
- [ ] Add rate limiting
- [ ] Add authentication/API keys
- [ ] Add proper logging and monitoring
- [ ] Add retry logic for API failures
- [ ] Add comprehensive error handling
- [ ] Add unit tests and integration tests

### Frontend Integration
- [ ] Build React/Next.js frontend
- [ ] Connect to backend API
- [ ] Implement Stripe checkout flow
- [ ] Add real-time progress updates
- [ ] Add property viewing interface

---

## Important Notes

- ✅ `.env` file is git-ignored (contains sensitive API keys)
- ✅ All API keys are stored in Railway environment variables
- ✅ Gunicorn configured for production via Procfile
- ✅ CORS enabled for frontend integration
- ⚠️  Currently uses in-memory storage (data lost on restart)
- ⚠️  Processing is synchronous (will timeout on large batches)
- ⚠️  No authentication implemented yet (public API)

---

## GitHub Repository
https://github.com/DigitalDawnAI/Prospect-Grid-v1

## Live API
https://web-production-a42df.up.railway.app

---

**Last Updated**: December 25, 2025
**Status**: ✅ Deployed and operational
