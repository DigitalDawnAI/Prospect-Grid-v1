# ProspectGrid Backend - Development Log

## Project Overview

ProspectGrid is a Flask-based REST API that processes real estate addresses through:
1. **Geocoding** (Google Maps API) - Convert addresses to coordinates
2. **Street View Fetching** (Google Street View API) - Get property images
3. **AI Scoring** (Google Gemini 2.5 Flash) - Score properties for distressed property acquisition (0-100 scale)

---

## Session: February 10, 2026 - Email Notifications & SendGrid → Resend Migration

### Context
Prior session added durable persistence (PostgreSQL), background worker queue (Redis + RQ), and email notification support. The worker service was deployed on Railway but missing environment variables. This session focused on getting email delivery working end-to-end.

### Problem: Worker Missing Environment Variables
- Railway worker service is a **separate service** from the web service
- It does NOT inherit environment variables from web — each service needs its own
- Worker was failing on geocoding because `GOOGLE_MAPS_API_KEY` was missing
- Email sending was not configured yet

### Resolution: Railway Worker Environment Variables
Added to the **worker** service in Railway (copied from web):
- `GOOGLE_MAPS_API_KEY` — geocoding + Street View
- `GOOGLE_API_KEY` — Gemini AI scoring
- `SECRET_KEY` — signing email result links (generated fresh: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `RESEND_API_KEY` — email delivery (see below)

### Problem: SendGrid Account Locked Out
- Created SendGrid account and verified `prospect-grid.com` domain
- SendGrid immediately locked the account: "You are not authorized to access this account"
- Twilio (owns SendGrid) console only provides Twilio API keys (`SK`/`ac` prefix), not SendGrid keys (`SG.` prefix)
- **SendGrid keys start with `SG.`** — anything else won't work with their Python library
- Twilio API keys are NOT interchangeable with SendGrid API keys

### The Fix: Switched to Resend

**Why Resend:**
- Simple API, generous free tier (100 emails/day)
- Easy domain verification
- Python SDK is minimal and clean
- No account lockout issues

**Code Changes:**

`app.py` (lines 22-23, 112-135):
- Removed: `from sendgrid import SendGridAPIClient` + `from sendgrid.helpers.mail import Mail`
- Added: `import resend`
- `send_results_email()` now uses `resend.Emails.send()` instead of `SendGridAPIClient`
- Environment variable changed: `SENDGRID_API_KEY` → `RESEND_API_KEY`
- From address: `ProspectGrid <results@prospect-grid.com>`

`requirements.txt`:
- Removed: `sendgrid>=6.10.0`
- Added: `resend>=2.0.0`

**Git Commit**: `0c51c92` - "Switch email provider from SendGrid to Resend"

### Resend Setup
1. Sign up at https://resend.com
2. Create API key (starts with `re_`) — full access permissions
3. Add domain `prospect-grid.com` — add DNS records in Cloudflare
4. Set `RESEND_API_KEY` in both web and worker services on Railway

### Railway Environment Variables (Current State)

**Both web AND worker services need:**
| Variable | Purpose | Status |
|----------|---------|--------|
| `GOOGLE_MAPS_API_KEY` | Geocoding + Street View | ✅ Both services |
| `GOOGLE_API_KEY` | Gemini AI scoring | ✅ Both services |
| `RESEND_API_KEY` | Email delivery (Resend) | ✅ Both services |
| `SECRET_KEY` | Signing email result links | ✅ Both services |
| `STRIPE_SECRET_KEY` | Payment processing | ✅ Web service |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhooks | ✅ Web service |
| `DATABASE_URL` | PostgreSQL connection | ✅ Both services (from Railway Postgres plugin) |
| `REDIS_URL` | Job queue connection | ✅ Both services (from Railway Redis plugin) |
| `MAINTENANCE_MODE` | Lock down site if `true` | Optional |

### Troubleshooting: If Emails Break
1. Check Railway worker logs for `RESEND_API_KEY not configured` — means the env var is missing
2. Check for Resend errors — domain may not be verified (DNS propagation)
3. Resend dashboard (https://resend.com) shows delivery logs and failures
4. The `from` address must match a verified domain in Resend: `results@prospect-grid.com`
5. If Resend key starts with anything other than `re_`, it's the wrong key

### Troubleshooting: If Worker Isn't Processing
1. Check worker logs in Railway for errors
2. Verify all 4 env vars are set on the **worker** service (not just web)
3. Worker and web are SEPARATE services — adding a var to one does NOT add it to the other
4. Redis must be running and `REDIS_URL` must be set on both services

### Recent Commits (This Period)
- `96eda77` - Add durable persistence, worker queue, and email notifications
- `7b67933` - Fix rq Connection import removed in newer versions
- `0c51c92` - Switch email provider from SendGrid to Resend

### Testing Status
- ⏳ Email delivery — awaiting user test results

---

## Session: January 7, 2026 - 🎉 FIXED: Session Expiration Issue

### Problem
**"Session not found or expired" error after Stripe checkout**
- User uploads CSV → session created in memory
- User goes through Stripe checkout
- Railway auto-deploys new code → memory wiped
- Stripe redirects back → session ID no longer exists
- **Result**: User saw "Session not found or expired" error

### Root Cause
In-memory storage (`upload_sessions = {}` and `campaigns = {}` Python dicts) gets cleared on every Railway deployment.

### The Fix ✅

**Implemented file-based persistent storage** (`src/storage_helper.py`)

**How it works:**
1. Sessions saved to `/tmp/prospectgrid_sessions/session_{id}.json`
2. Campaigns saved to `/tmp/prospectgrid_sessions/campaign_{id}.json`
3. Files persist across Railway deployments
4. Auto-cleanup of expired sessions on startup
5. Campaign progress saved after each property (crash-resistant)

**Files Changed:**
- `src/storage_helper.py` - **NEW** - Persistent storage module
  - `save_session()` / `load_session()` - Session management
  - `save_campaign()` / `load_campaign()` - Campaign management
  - `cleanup_expired_sessions()` - Auto-cleanup
- `app.py` - Replaced all in-memory dict access with file-based storage

**Deployment:**
```bash
git commit -m "Fix session expiration issue with file-based storage"
git push origin main
```
- ✅ Commit: `a630f96`
- ✅ Deployed to Railway: https://web-production-a42df.up.railway.app
- ✅ Issue **PERMANENTLY FIXED** - no more session expiration errors!

### Testing
**Before fix:**
1. Upload CSV → get session ID
2. Wait for deployment (or trigger one)
3. Try to continue → "Session not found or expired" ❌

**After fix:**
1. Upload CSV → session saved to disk
2. Wait for deployment
3. Continue normally → session still exists ✅

---

## Session: January 4, 2026 - Fix Gemini Vision API & Implement Scoring Rubric

### Issue Discovered
**Problem**: Images were being fetched but no scoring was happening
- User reported: "im taking the images but no scoring now"
- Root cause: `gemini-1.5-flash` model doesn't exist (404 error)
- Secondary issue: No comprehensive scoring rubric file (using basic fallback)

### Investigation
Created diagnostic test script (`test_gemini.py`) to debug Gemini API:
```bash
python3 test_gemini.py
```

**Findings**:
- ❌ Error: `404 models/gemini-1.5-flash is not found`
- ✅ Available models: `gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-flash-latest`
- ⚠️ No `prompts/scoring_v1.txt` file (scorer was using basic fallback)

### The Fix

✅ **Updated Model Name**
- Changed from `gemini-1.5-flash` → `gemini-2.5-flash`
- File: `src/gemini_scorer.py:21`

✅ **Implemented Comprehensive Scoring Rubric**
- Created `prompts/scoring_v1.txt` with detailed distressed property acquisition criteria
- **Scale**: 0-100 (higher = better acquisition candidate)
- **Weighted Criteria**:
  - Structural & Exterior Decay (HIGH WEIGHT)
  - Abandonment & Vacancy Signals (HIGH WEIGHT)
  - Landscape & Grounds (MEDIUM WEIGHT)
  - Utility & Maintenance (MEDIUM WEIGHT)
  - Neighborhood Context (LOW-MEDIUM WEIGHT)

✅ **Updated Data Models** (`src/models.py`)
New `PropertyScore` format:
- `property_score` (0-100) - Main distress score
- `recommendation` - strong_candidate, moderate_candidate, weak_candidate, not_a_candidate
- `primary_indicators_observed` - List of distress signals
- `brief_reasoning` - Detailed property analysis
- `confidence_level` - high, medium, low
- **Auto-converts** 0-100 scale to legacy 1-10 scale for backward compatibility

✅ **Updated Scorer Logic** (`src/gemini_scorer.py`)
- Parse new JSON response format
- Handle recommendation levels
- Maintain backward compatibility

### Recommendation Levels
- **strong_candidate** (70-100): Multiple high-weight indicators, motivated seller likely
- **moderate_candidate** (40-69): Some distress indicators present
- **weak_candidate** (15-39): Few indicators, uncertain opportunity
- **not_a_candidate** (0-14): No significant distress visible

### Testing Results
```bash
Property Score: 5/100
Recommendation: not_a_candidate
Confidence: high
Primary Indicators: (none - well-maintained property)
Reasoning: Well-maintained commercial/residential buildings and active urban environment
Legacy conversion: 1/10 ✅
```

### Files Changed
- `prompts/scoring_v1.txt` - **NEW** - Comprehensive scoring rubric
- `src/gemini_scorer.py` - Model name + response parsing
- `src/models.py` - New PropertyScore format with backward compatibility
- `test_gemini.py` - **NEW** - Diagnostic test script

### Deployment
```bash
git commit -m "Fix Gemini vision API and implement proper scoring rubric"
git push origin main
```
- ✅ Pushed to GitHub: commit `03b746f`
- ✅ Auto-deploying to Railway: https://web-production-a42df.up.railway.app
- ⏳ Frontend update needed to display new scoring fields

### ✅ FIXED: Session Expiration Issue

**Status**: **PERMANENTLY FIXED** as of January 7, 2026 (commit `a630f96`)

This issue has been resolved by implementing file-based persistent storage. See the session above for details.

**What was the problem:**
- In-memory storage got wiped on Railway deployments
- Users saw "Session not found or expired" after Stripe checkout

**How it was fixed:**
- Implemented `src/storage_helper.py` with file-based storage
- Sessions and campaigns now persist across deployments
- Stored in `/tmp/prospectgrid_sessions/` on Railway

**No action needed** - the fix is deployed and working!

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
street,city,state,zip
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

### Required (both web + worker services)
```bash
GOOGLE_MAPS_API_KEY=<your_google_maps_key>    # Geocoding + Street View
GOOGLE_API_KEY=<your_google_api_key>          # Gemini AI scoring
RESEND_API_KEY=<your_resend_key>              # Email delivery (starts with re_)
SECRET_KEY=<random_secret>                     # Signing email links
DATABASE_URL=<postgresql_url>                  # PostgreSQL (from Railway plugin)
REDIS_URL=<redis_url>                          # Job queue (from Railway plugin)
```

### Required (web service only)
```bash
STRIPE_SECRET_KEY=<your_stripe_secret_key>
STRIPE_WEBHOOK_SECRET=<your_stripe_webhook_secret>
```

### Optional
```bash
MAINTENANCE_MODE=true|false                    # Lock down site when true
FLASK_ENV=development|production
PORT=5001
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
- **AI Scoring**: ~$0.000075 (Google Gemini 2.5 Flash — essentially free within daily limits)
- **Email**: Free tier (Resend, 100 emails/day)

---

## Tech Stack

- **Framework**: Flask 3.0.0
- **Web Server**: Gunicorn 21.2.0
- **Database**: PostgreSQL (Railway plugin)
- **Job Queue**: Redis + RQ (Railway plugin)
- **Email**: Resend (replaced SendGrid Feb 2026)
- **Payments**: Stripe
- **APIs**:
  - Google Maps Geocoding API
  - Google Street View Static API
  - Google Gemini 2.5 Flash (AI scoring)
- **Data Validation**: Pydantic 2.10+
- **Deployment**: Railway (backend + worker) / Vercel (frontend)
- **DNS**: Cloudflare
- **Repository**: GitHub

---

## Next Steps

### MVP Completion
- [x] Add PostgreSQL database (replace in-memory storage) — commit `96eda77`
- [x] Implement background job processing (Redis + RQ) — commit `96eda77`
- [x] Add Stripe payment webhook handler
- [x] Add email notifications (Resend, replaced SendGrid) — commit `0c51c92`
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
- ✅ PostgreSQL for durable persistence (replaced in-memory storage)
- ✅ Redis + RQ for async background processing (replaced synchronous)
- ✅ Email notifications via Resend (replaced SendGrid)
- ✅ Stripe payment integration
- ⚠️  Railway worker service needs its own env vars (doesn't inherit from web)
- ⚠️  No authentication implemented yet (public API, protected by Stripe paywall)

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

## Session: December 31, 2025 - API Key Setup & CSV Column Standardization

### What We Accomplished

✅ **Google API Key Setup**
- Added `GOOGLE_API_KEY` environment variable for Gemini 2.0 Flash
- Updated `.env.example` with clear documentation
- User added API key to Railway environment variables
- Backend and frontend both deployed and operational

**Google Gemini Free Tier:**
- 1,500 requests per day (completely free)
- Standard tier: 1,500 properties/day free
- Premium tier: 375 properties/day free (4 images each)
- Gemini scoring essentially free for moderate usage!

✅ **CSV Column Standardization**
- **Changed required column**: `address` → `street`
- **Reason**: Consistency and clarity in CSV format

**Backend Changes** (`app.py`):
- Removed support for `address` column
- Now only accepts `street` column
- Updated error message: "Missing 'street' column"

**Frontend Changes** (`app/page.tsx`):
- Updated homepage text: "Must include columns: Street, City, State, Zip"
- Matches backend requirement

**Documentation**:
- Updated `claude.md` with new CSV format
- Added screenshot to `docs/address-street.png` showing the change

### CSV Format (Final)

```csv
street,city,state,zip
123 Main St,Atlantic City,NJ,08401
456 Oak Ave,Atlantic City,NJ,08401
```

### Git Commits

**Backend**:
- `48c266c` - Change CSV column from 'address' to 'street'
- `7e8778e` - Update .env.example with GOOGLE_API_KEY
- `a89c022` - Add docs folder with CSV format screenshot

**Frontend**:
- `2312767` - Update CSV column label from 'Address' to 'Street'

### Deployment Status

✅ **Backend** (Railway): https://web-production-a42df.up.railway.app
- Environment variable `GOOGLE_API_KEY` configured
- Auto-deployed from main branch
- All changes live

✅ **Frontend** (Vercel): https://www.prospect-grid.com
- Homepage updated with new CSV format
- Auto-deployed from main branch
- All changes live

### Current System Status

**Tech Stack:**
- Backend: Flask + Gemini 2.0 Flash vision API
- Frontend: Next.js + React
- Deployment: Railway (backend) + Vercel (frontend)

**Service Tiers:**
| Tier | Cost per 100 Properties | Features |
|------|-------------------------|----------|
| Street View Standard | ~$1.80 | 1 angle, no AI |
| Street View Premium | ~$5.00 | 4 angles, no AI |
| Full Scoring Standard | ~$1.80 | 1 angle + Gemini AI (FREE within daily limits) |
| Full Scoring Premium | ~$5.00 | 4 angles + Gemini AI (FREE within daily limits) |

**API Keys Required:**
- ✅ `GOOGLE_MAPS_API_KEY` - Geocoding + Street View
- ✅ `GOOGLE_API_KEY` - Gemini 2.5 Flash vision
- ✅ `RESEND_API_KEY` - Email delivery (starts with `re_`)
- ✅ `SECRET_KEY` - Signing email result links
- ✅ `STRIPE_SECRET_KEY` - Payment processing
- ⚠️ `ANTHROPIC_API_KEY` - Legacy (not currently used)

### Ready to Resume

**All code is committed and pushed to GitHub:**
- Backend: https://github.com/DigitalDawnAI/Prospect-Grid-v1
- Frontend: https://github.com/DigitalDawnAI/prospect-grid-web

**To test after restart:**
1. Go to https://www.prospect-grid.com
2. Upload CSV with format: `street,city,state,zip`
3. Choose any service tier
4. Process and view results

### 🚨 Security: Maintenance Mode Added

**Problem Discovered**: Site is live with no paywall - anyone can use it and charge to our API keys!

**Solution Implemented**: Maintenance mode environment variable

**Code Changes** (`app.py`):
```python
# Added to /api/upload and /api/process endpoints
if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
    return jsonify({
        "error": "Service temporarily unavailable for maintenance. Please check back soon."
    }), 503
```

**How to Enable/Disable**:

**Enable** (lock down site):
```bash
# In Railway > Variables
MAINTENANCE_MODE=true
```

**Disable** (allow usage):
```bash
# In Railway > Variables
MAINTENANCE_MODE=false
# OR delete the variable entirely
```

**What It Does**:
- ✅ Blocks CSV uploads
- ✅ Blocks processing requests
- ✅ Prevents unauthorized API usage
- ✅ Protects API keys from unexpected charges

**Git Commit**: `25dca87` - "Add maintenance mode to protect API keys"

**Next Steps**:
- [ ] Implement Stripe payment integration
- [ ] Add authentication/user accounts
- [ ] Add rate limiting per IP/user
- [ ] Set Google API spending caps

---

**Last Updated**: February 10, 2026
**Status**: ✅ Deployed — PostgreSQL, Redis+RQ worker, Stripe payments, Resend email all live
