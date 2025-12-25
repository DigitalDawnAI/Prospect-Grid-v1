# ProspectGrid Backend API

Flask API that wraps existing ProspectGrid modules (geocoder, streetview, scorer).

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
    "streetview_only": {
      "subtotal": 2.84,
      "price": 4.26
    },
    "full_scoring": {
      "subtotal": 8.77,
      "price": 13.16
    }
  }
}
```

### 3. Start Processing
```bash
POST /api/process/{session_id}
Content-Type: application/json

{
  "service_level": "full_scoring",
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
  "status": "complete",
  "total_properties": 237,
  "success_count": 225,
  "failed_count": 12,
  "properties": [
    {
      "address_full": "123 Main St, Atlantic City, NJ 08401",
      "prospect_score": 8,
      "score_reasoning": "...",
      "streetview_url": "...",
      ...
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
