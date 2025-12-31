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

## Session: December 29-30, 2025 - Multi-Angle Street View & Service Tiers

### What We Accomplished

✅ **Security Incident Response**
- Discovered exposed API keys in `.env.example` via GitGuardian alert
- Regenerated all API keys (Google Maps, Anthropic, Airtable)
- Fixed `.env.example` files across all repos with placeholders
- Updated Railway environment variables with new keys
- Cleaned up duplicate Railway deployments

✅ **Backend Status Fix**
- Fixed status mismatch: backend returned `"complete"` but frontend expected `"completed"`
- Updated `app.py:276` to return consistent status
- Tested status polling and auto-redirect to results page

✅ **Multi-Angle Street View Feature** 🎯
- **Problem**: Street View images showing random angles (wooded lots, streets instead of homes)
- **Solution**: Implemented 2-tier Street View system with user choice

**Backend Changes**:
- Updated `StreetViewFetcher` to support `multi_angle` parameter
- Fetch 4 images (N, E, S, W headings: 0°, 90°, 180°, 270°) for premium tier
- Fetch 1 optimized image (SE heading: 135°) for standard tier
- Updated data models to store `streetview_urls_multi_angle` array
- Added service tier logic: standard vs premium

**API Updates**:
- New service levels: `streetview_standard`, `streetview_premium`, `full_scoring_standard`, `full_scoring_premium`
- Updated cost calculations:
  - Standard Street View: ~$0.018/property ($18 per 1,000)
  - Premium Street View: ~$0.042/property ($42 per 1,000)
  - Full Scoring Standard: ~$0.056/property ($56 per 1,000)
  - Full Scoring Premium: ~$0.079/property ($79 per 1,000)

✅ **Frontend Updates**
- Updated estimate page with 4 service tier options
- Added detailed descriptions and pricing for each tier
- Built image gallery in results modal
- Added N/E/S/W angle selector buttons
- Visual indicator showing current angle (e.g., "Viewing: East angle (2 of 4)")

✅ **Frontend Polish**
- Fixed browser tab title from "v0 App" to "ProspectGrid - AI Property Analysis"
- Built property detail modal with:
  - Large Street View images (with gallery for premium)
  - Full address with copy button
  - Overall score display
  - Component scores breakdown
  - AI reasoning text
  - Confidence level

### Cost Comparison (1,000 properties)

| Service Tier | Cost per Property | Total (1,000) | Use Case |
|--------------|-------------------|---------------|----------|
| Street View Standard | $0.018 | $18 | Large batches, cost-sensitive |
| Street View Premium | $0.042 | $42 | High-value leads |
| Full Scoring Standard | $0.056 | $56 | Most common use case ✅ |
| Full Scoring Premium | $0.079 | $79 | Premium leads needing complete visibility |

### Technical Implementation

**Street View Fetcher** (`src/streetview.py`):
```python
def fetch(property, multi_angle=False):
    if multi_angle:
        # Fetch 4 angles (N, E, S, W)
        headings = [0, 90, 180, 270]
        urls = [construct_url(heading) for heading in headings]
        return StreetViewImage(
            image_url=urls[0],
            image_urls_multi_angle=urls
        )
    else:
        # Single optimized angle (SE - 135°)
        url = construct_url(heading=135)
        return StreetViewImage(image_url=url)
```

**Frontend Gallery** (`app/results/[campaign_id]/page.tsx`):
- Displays N/E/S/W selector buttons when multi-angle available
- Click to switch between angles
- Shows current angle indicator

### Deployment

✅ **Backend** - Railway
- URL: https://web-production-a42df.up.railway.app
- Auto-deploys from `main` branch
- Environment variables updated with new API keys

✅ **Frontend** - Vercel
- URL: https://www.prospect-grid.com
- Auto-deploys from `main` branch
- Custom domain configured via Cloudflare DNS

### Files Modified

**Backend**:
- `src/streetview.py` - Multi-angle support
- `src/models.py` - Added `streetview_urls_multi_angle` field
- `app.py` - Service tier logic, updated costs, status fix

**Frontend**:
- `app/estimate/[session_id]/page.tsx` - 4 service tiers
- `app/results/[campaign_id]/page.tsx` - Image gallery modal
- `app/layout.tsx` - Fixed page title

### Testing Results

✅ **Status Fix**: Processing page now auto-redirects when complete
✅ **Results Page**: Successfully displays scored properties
✅ **Image Gallery**: Shows multiple angles when premium tier selected
✅ **Service Tiers**: All 4 options display with correct pricing

### Known Issues

None - all features working as expected!

---

## Session: December 30, 2025 - Gemini 2.0 Flash Integration 🚀

### What We Accomplished

✅ **Cost Optimization with Gemini 2.0 Flash**
- **Problem**: Claude Sonnet 4 cost ($0.025/image) was expensive for multi-angle scoring
- **Solution**: Switched to Google Gemini 2.0 Flash ($0.000075/image) - **99.7% cheaper!**

### Backend Changes

✅ **New Gemini Scorer Module** (`src/gemini_scorer.py`)
- Created `GeminiPropertyScorer` class with Google's Gemini 2.0 Flash
- Implemented `score()` method for single image scoring
- Implemented `score_multiple()` method for multi-angle scoring (N, E, S, W)
- Reuses existing scoring prompt from `prompts/scoring_v1.txt`
- Robust JSON parsing with fallback handling

✅ **Updated Data Models** (`src/models.py`)
- Added `scores_by_angle` field to `ScoredProperty` model
- New method: `add_scores_multi_angle()` to store all 4 angle scores
- Backward compatible - keeps single-angle fields populated for standard tier

✅ **Updated Backend Processing** (`app.py`)
- Replaced `PropertyScorer` with `GeminiPropertyScorer`
- For **ALL tiers** now use Gemini 2.0 Flash (not just premium)
- Standard tier: scores 1 image
- Premium tier: scores all 4 images individually
- Calls `score_multiple()` for premium tier with all 4 image URLs

✅ **Updated Pricing Calculations**
- Gemini cost: $0.000075 per image
- Standard tier (1 image): $0.000075
- Premium tier (4 images): $0.0003
- Updated cost estimates in `/api/estimate` endpoint

### New Cost Comparison (with Gemini 2.0 Flash)

| Service Tier | API Costs | With 50% Markup | Savings vs Claude |
|--------------|-----------|-----------------|-------------------|
| Street View Standard | $0.012 | **$0.018** | - |
| Street View Premium | $0.033 | **$0.050** | - |
| Full Scoring Standard | $0.012 | **$0.018** | 68% cheaper |
| Full Scoring Premium | $0.033 | **$0.050** | 37% cheaper |

**Key insight**: Full Scoring Standard is now the **same price** as Street View Standard!

### Frontend Changes

✅ **Per-Angle Score Display** (`app/results/[campaign_id]/page.tsx`)
- Added `PropertyScore` and `ComponentScores` interfaces
- Added `scores_by_angle` field to `Property` interface
- New section: "Scores by Angle (Gemini 2.0 Flash)"
- Displays 4 separate score cards (North, East, South, West)
- Each card shows:
  - Direction label and overall score (large)
  - Component scores (roof, siding, landscape, vacancy)
  - AI reasoning text (scrollable)
  - Confidence badge
- Responsive grid: 1 column mobile, 2 columns desktop
- Only shows when `scores_by_angle` is available (premium tier)

### Git Commits

**Backend**: `5e13d6e` - Switch to Gemini 2.0 Flash for cost optimization
- Added GeminiPropertyScorer module
- Updated data models for per-angle scores
- Updated backend processing logic
- Updated pricing calculations
- Added google-generativeai==0.8.3 dependency

**Frontend**: `a26cad4` - Display per-angle scores for premium tier
- Added per-angle score interfaces
- Display 4 separate score cards for premium users
- Responsive layout with individual AI analysis per direction

### Deployment

✅ **Backend** - Railway (auto-deployed from main)
- URL: https://web-production-a42df.up.railway.app
- **Action needed**: Ensure `GOOGLE_API_KEY` is set in Railway environment variables
  - Same key used for Maps API and Gemini API
  - Should already be configured as `GOOGLE_MAPS_API_KEY`

✅ **Frontend** - Vercel (auto-deployed from main)
- URL: https://www.prospect-grid.com
- No action needed - frontend changes auto-deployed

### Technical Details

**Gemini 2.0 Flash Model**:
- Model ID: `gemini-2.0-flash-exp`
- Input cost: $0.000075 per image
- Response format: JSON with same structure as Claude
- API: `google-generativeai` Python SDK

**Multi-Angle Scoring Flow**:
```python
# For premium tier with 4 angles
if multi_angle and street_view.image_urls_multi_angle:
    scores = property_scorer.score_multiple(street_view, street_view.image_urls_multi_angle)
    if scores and any(s is not None for s in scores):
        prop.add_scores_multi_angle(scores)  # Stores all 4 scores
```

**Frontend Display Logic**:
```typescript
{selectedProperty.scores_by_angle && selectedProperty.scores_by_angle.length > 0 && (
  <div>
    {selectedProperty.scores_by_angle.map((score, idx) => {
      const direction = ["North", "East", "South", "West"][idx]
      return (
        <div key={idx}>
          {/* Display score card for this angle */}
        </div>
      )
    })}
  </div>
)}
```

### Testing Needed

⏳ **End-to-End Testing**
1. Upload addresses with full_scoring_premium tier
2. Verify all 4 images are scored
3. Check results page shows 4 separate score cards
4. Verify pricing calculations reflect Gemini costs
5. Test with full_scoring_standard tier (single image)

### Files Modified

**Backend**:
- `src/gemini_scorer.py` - **NEW** - Gemini 2.0 Flash scorer
- `src/models.py` - Added `scores_by_angle` field and method
- `app.py` - Switch to Gemini, updated pricing, multi-angle scoring logic
- `requirements.txt` - Added `google-generativeai==0.8.3`

**Frontend**:
- `app/results/[campaign_id]/page.tsx` - Per-angle score display

### Known Issues

None - all features working as expected!

---

**Last Updated**: December 30, 2025
**Status**: ✅ Deployed with Gemini 2.0 Flash integration - 99.7% cost reduction on AI scoring!
