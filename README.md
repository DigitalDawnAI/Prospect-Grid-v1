# ProspectGrid Backend API

Flask REST API for AI-powered real estate property analysis with multi-angle Street View imagery and condition scoring.

## Features

- 🗺️ **Address Geocoding** - Convert addresses to precise coordinates
- 📸 **Multi-Angle Street View** - Fetch 1 or 4 angles per property
- 🤖 **AI Condition Scoring** - Claude Sonnet 4 analyzes property condition (1-10 scale)
- 💰 **Flexible Service Tiers** - Choose between standard and premium options
- 📊 **Real-time Progress** - Track processing status via API

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env

# Edit .env with your API keys
nano .env

# Run locally
python app.py
```

## API Endpoints

### 1. Upload CSV
```bash
POST /api/upload
Content-Type: multipart/form-data

# Upload a CSV file with addresses
curl -X POST -F "file=@addresses.csv" http://localhost:5000/api/upload
```

Response:
```json
{
  "session_id": "uuid",
  "address_count": 237,
  "errors": []
}
```

### 2. Get Cost Estimate
```bash
GET /api/estimate/{session_id}
```

Response:
```json
{
  "address_count": 237,
  "costs": {
    "streetview_standard": {
      "subtotal": 2.84,
      "price": 2.84,
      "description": "1 optimized angle"
    },
    "streetview_premium": {
      "subtotal": 7.82,
      "price": 7.82,
      "description": "4 angles (N, E, S, W)"
    },
    "full_scoring_standard": {
      "subtotal": 2.86,
      "price": 2.86,
      "description": "AI scoring + 1 angle"
    },
    "full_scoring_premium": {
      "subtotal": 7.89,
      "price": 7.89,
      "description": "AI scoring + 4 angles"
    }
  }
}
```

## Service Tiers

**At-cost pricing** - You pay exactly what the APIs cost, with zero markup.

| Tier | Features | Cost/Property | Best For |
|------|----------|---------------|----------|
| **Street View Standard** | 1 optimized angle (135° SE) | $0.012 | Large batches, cost-sensitive |
| **Street View Premium** | 4 angles (N, E, S, W) | $0.033 | High-value leads |
| **Full Scoring Standard** ⭐ | AI scoring + 1 angle | $0.012 | Most common use case |
| **Full Scoring Premium** | AI scoring + 4 angles | $0.033 | Premium leads |

### 3. Start Processing
```bash
POST /api/process/{session_id}
Content-Type: application/json

{
  "service_level": "streetview_standard" | "streetview_premium" | "full_scoring_standard" | "full_scoring_premium",
  "email": "user@example.com",
  "payment_intent_id": "pi_xxx"
}
```

Response:
```json
{
  "campaign_id": "uuid",
  "status": "processing",
  "estimated_time_minutes": 11.85
}
```

### 4. Get Processing Status
```bash
GET /api/status/{campaign_id}
```

Response:
```json
{
  "campaign_id": "uuid",
  "status": "processing",
  "total_properties": 237,
  "processed_count": 45,
  "success_count": 43,
  "failed_count": 2,
  "progress_percent": 19.0
}
```

### 5. Get Results
```bash
GET /api/results/{campaign_id}
```

Response:
```json
{
  "campaign_id": "uuid",
  "status": "completed",
  "total_properties": 237,
  "success_count": 225,
  "failed_count": 12,
  "properties": [
    {
      "address_full": "123 Main St, Atlantic City, NJ 08401",
      "prospect_score": 8,
      "score_reasoning": "Moderate distress signals...",
      "streetview_url": "https://maps.googleapis.com/...",
      "streetview_urls_multi_angle": [
        "https://maps.googleapis.com/...heading=0",
        "https://maps.googleapis.com/...heading=90",
        "https://maps.googleapis.com/...heading=180",
        "https://maps.googleapis.com/...heading=270"
      ],
      "score_roof": 7,
      "score_siding": 8,
      "score_landscaping": 6,
      "score_vacancy": 9,
      "confidence": "high"
    }
  ]
}
```

### 6. Get Single Property
```bash
GET /api/property/{campaign_id}/{property_index}
```

## Deployment

### Railway
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Add environment variables
railway variables set GOOGLE_MAPS_API_KEY=xxx
railway variables set ANTHROPIC_API_KEY=xxx

# Deploy
railway up
```

### Render
1. Connect GitHub repo
2. Set environment variables
3. Deploy

## TODO for Production

- [ ] Replace in-memory storage with PostgreSQL
- [ ] Add Celery/RQ for background job processing
- [ ] Add Stripe payment webhook handler
- [ ] Add rate limiting
- [ ] Add authentication (optional)
- [ ] Add email notifications (SendGrid)
- [ ] Add CSV export endpoint
- [ ] Add proper error handling and retry logic
