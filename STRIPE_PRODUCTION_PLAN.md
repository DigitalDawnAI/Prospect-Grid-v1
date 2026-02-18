# Stripe Production Planning: Forensic Analysis & Go-Live Plan

**Date**: 2026-02-18
**Repository**: DigitalDawnAI/Prospect-Grid-v1
**Author**: Claude (Opus 4.5) - Senior Backend Engineer / Payments Integrator
**Status**: READ-ONLY FORENSIC ANALYSIS - No code changes made

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Step 1: Backend Forensic Scan](#step-1-backend-forensic-scan)
3. [Step 2: Frontend Touchpoint Scan](#step-2-frontend-touchpoint-scan)
4. [Step 3: Go-Live Plan](#step-3-go-live-plan)
5. [Step 4: Admin Override Design](#step-4-admin-override-design)
6. [Step 5: Open Questions / Unknowns](#step-5-open-questions--unknowns)
7. [Appendix: Quick Reference](#appendix-quick-reference)

---

## Executive Summary

### Current State
| Aspect | Status | Notes |
|--------|--------|-------|
| Stripe SDK | ✅ Integrated | `stripe==11.1.1` in requirements |
| Checkout Sessions | ✅ Working | Dynamic pricing per campaign |
| Payment Verification | ✅ Working | Manual verification via API call |
| Webhook Handler | ❌ Missing | **Critical gap** |
| Persistent Storage | ❌ Missing | Uses ephemeral `/tmp` |
| Test Mode | ✅ Active | `sk_test_` keys assumed |

### Critical Actions for Production
1. **Implement webhook handler** with signature verification
2. **Add idempotency** to prevent duplicate campaigns
3. **Consider persistent storage** (Redis/PostgreSQL)
4. **Configure live Stripe keys** in production environment
5. **QA with real card** before public launch

---

## Step 1: Backend Forensic Scan

### A) Component Inventory Table

| Component | File Path(s) | What It Does | Current Assumptions | Risks/Unknowns |
|-----------|--------------|--------------|---------------------|----------------|
| **Stripe SDK Init** | `app.py:14,45` | Imports `stripe` module; loads `STRIPE_SECRET_KEY` from env at startup | Key is set before any request; single key for all operations | ⚠️ No key validation at startup; server starts even if key is missing/invalid |
| **Checkout Session Creation** | `app.py:171-256` | Creates Stripe checkout session with dynamic pricing based on property count | Only `full_scoring_standard` tier; 50% markup; $0.50 minimum | ⚠️ No idempotency key; duplicate sessions possible on retry |
| **Payment Verification (primary)** | `app.py:259-347` | Retrieves Stripe session, verifies `payment_status=="paid"`, creates campaign, starts processing | Session exists in Stripe; upload session still valid (24h TTL) | ⚠️ Race condition: user can hit verify multiple times creating duplicate campaigns |
| **Payment Verification (alt)** | `app.py:350-454` | Alternative endpoint with same logic; also validates session_id match | Same as above | ⚠️ Redundant endpoint; consolidation opportunity |
| **Pricing Calculation** | `app.py:143-148,214-221` | Calculates cost: `(address_count * 0.005) + (address_count * 0.007) + (address_count * 0.000075)` then `* 1.5` | Cost model is static; minimum $0.50 | Cost model embedded in code, not configurable |
| **Campaign Persistence** | `src/storage_helper.py:103-156` | JSON files in `/tmp/prospectgrid_sessions/` | Ephemeral storage; single Railway replica | ⚠️ **CRITICAL**: /tmp is lost on redeploy; no webhook to recover |
| **Processing Pipeline** | `app.py:474-562` | Background thread processes addresses: geocode → streetview → AI score | Thread survives request; campaign stored incrementally | ⚠️ Thread dies if instance restarts mid-processing |
| **Webhook Handler** | **MISSING** | No webhook endpoint exists | N/A | ⚠️ **CRITICAL**: No `checkout.session.completed` handler; no payment recovery |
| **Webhook Secret** | `.env.example:20` | `STRIPE_WEBHOOK_SECRET` defined but **not used** | N/A | Must implement before production |
| **Maintenance Mode** | `app.py:60-68,178-186,266-274,357-365` | `MAINTENANCE_MODE=true` env var blocks uploads/payments with 503 | Used as kill switch | Functional; good safety mechanism |
| **Error Handling** | `app.py:342-347,449-454` | Catches `stripe.error.StripeError` separately | Logs errors with exc_info | Good practice; errors logged |

### B) Current Stripe Integration Shape

#### SDK Initialization
```python
# app.py:14
import stripe

# app.py:45
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
```

**Environment Variables**:
- `STRIPE_SECRET_KEY` - **Required**, loaded at startup
- `STRIPE_WEBHOOK_SECRET` - Defined but unused
- `STRIPE_PUBLISHABLE_KEY` - Defined but unused (frontend would need this for Stripe.js)

#### Payment Type: ONE-TIME PAYMENT ONLY
- Uses `mode="payment"` (line 238)
- No subscriptions
- No usage-based metering
- Dynamic `price_data` per checkout (not pre-created Stripe Prices)

#### Checkout Session Creation
**Location**: `app.py:223-247`

```python
checkout_session = stripe.checkout.Session.create(
    payment_method_types=["card"],
    line_items=[{
        "price_data": {
            "currency": "usd",
            "product_data": {
                "name": "ProspectGrid - Full AI Scoring Standard",
                "description": f"AI property analysis for {address_count} properties",
            },
            "unit_amount": amount_cents,  # Dynamic, min $0.50
        },
        "quantity": 1,
    }],
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
```

**Key Observations**:
- `{CHECKOUT_SESSION_ID}` is a Stripe template variable (auto-replaced by Stripe)
- No `customer` parameter (creates new customer each time)
- Metadata captures essential context for verification
- Email is optional (`customer_email=email` where email may be None)
- No idempotency key used

#### Webhook Endpoint: **NOT IMPLEMENTED**
- `STRIPE_WEBHOOK_SECRET` defined in `.env.example:20`
- No `/webhook` or `/api/stripe/webhook` route exists
- No `stripe.Webhook.construct_event()` usage found in codebase

#### Payment State Persistence
```python
# app.py:309-324 - Campaign data includes payment references
campaign_data = {
    "payment_intent_id": checkout_session.payment_intent,
    "stripe_session_id": stripe_session_id,
    # ... other fields
}
save_campaign(campaign_id, campaign_data)
```

**Storage Location**: `/tmp/prospectgrid_sessions/campaign_{id}.json`
- **EPHEMERAL**: Railway's `/tmp` is wiped on redeploy
- No database persistence
- No backup/recovery mechanism

### C) Coupling to Scanning Workflow

#### Payment Gating Flow
```
1. POST /api/upload
   → Returns session_id
   → No payment required

2. GET /api/estimate/{session_id}
   → Returns pricing info
   → No payment required

3. POST /api/create-checkout-session
   → Creates Stripe session
   → Returns checkout_url
   → Initiates payment flow

4. [User redirected to Stripe hosted checkout page]
   → User enters card details
   → Stripe processes payment

5. [Stripe redirects to success URL]
   → https://www.prospect-grid.com/processing/{CHECKOUT_SESSION_ID}

6. POST /api/verify-payment/{stripe_session_id}
   → PAYMENT GATE: Verifies payment_status == "paid"
   → Creates campaign record
   → Starts background processing
   → Returns campaign_id

7. GET /api/status/{campaign_id}
   → Poll for progress

8. GET /api/results/{campaign_id}
   → Retrieve final results
```

#### Payment Gate Location
**File**: `app.py:276-278`
```python
checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
if checkout_session.payment_status != "paid":
    return jsonify({"error": "Payment not completed"}), 400
```

#### Cost Per Property Calculation
**File**: `app.py:214-221`

```python
geocoding_cost = address_count * 0.005      # Google Geocoding API
streetview_cost = address_count * 0.007     # Google Street View API
gemini_cost_per_image = 0.000075            # Gemini 2.0 Flash
scoring_cost = address_count * gemini_cost_per_image

total = geocoding_cost + streetview_cost + scoring_cost  # ~$0.012/address
final_price = total * 1.5                                 # 50% margin
amount_cents = max(int(final_price * 100), 50)           # Min $0.50
```

**Cost Breakdown**:
| Component | Cost per Property | Source |
|-----------|-------------------|--------|
| Geocoding | $0.005 | Google Maps API |
| Street View | $0.007 | Google Street View API |
| AI Scoring | $0.000075 | Gemini 2.0 Flash |
| **Subtotal** | **~$0.012** | Actual API cost |
| **Customer Price** | **~$0.018** | With 50% markup |
| **Minimum Charge** | **$0.50** | Stripe minimum |

**Example**: 100 properties = $1.20 cost → $1.80 charged

---

## Step 2: Frontend Touchpoint Scan

> **Note**: Frontend repository (`github.com/DigitalDawnAI/prospect-grid-web`) was not directly accessible. Analysis based on backend API contracts and success/cancel URLs.

### Inferred Frontend Routes

| Frontend Route | Purpose | Backend Endpoint |
|----------------|---------|------------------|
| `/estimate/{session_id}` | Display pricing; checkout button | `GET /api/estimate/{session_id}` |
| Checkout button click | Initiate Stripe payment | `POST /api/create-checkout-session` |
| Stripe hosted page | Payment entry | N/A (Stripe-managed) |
| `/processing/{CHECKOUT_SESSION_ID}` | Post-payment landing | `POST /api/verify-payment/{session_id}` |
| Progress polling | Show progress bar | `GET /api/status/{campaign_id}` |
| `/results/{campaign_id}` | Display final results | `GET /api/results/{campaign_id}` |

### Frontend Implementation Notes

1. **No Stripe.js Required**: Backend uses Stripe-hosted checkout, not embedded Elements
2. **No Publishable Key Needed Client-Side**: All Stripe interaction is server-side
3. **Success URL Hardcoded**: `https://www.prospect-grid.com/processing/...`
   - Verify this matches actual production domain
4. **Cancel URL Dynamic**: Returns to `/estimate/{session_id}`

### Frontend Gaps/Considerations for Go-Live

| # | Consideration | Action Needed |
|---|---------------|---------------|
| 1 | Verify production domain matches hardcoded URLs | Check `www.prospect-grid.com` is correct |
| 2 | Error handling for failed payments | Frontend should handle verify-payment errors gracefully |
| 3 | Timeout handling | Consider what happens if user never returns from Stripe |
| 4 | Mobile responsiveness | Stripe checkout is mobile-friendly by default |

---

## Step 3: Go-Live Plan

### Phase 0: Baseline Readiness Checks

- [ ] **0.1** Verify test mode flows end-to-end manually:
  - Upload CSV → get estimate → create checkout → pay with test card `4242424242424242` → verify redirect → confirm campaign created → verify results
- [ ] **0.2** Confirm `STRIPE_SECRET_KEY` is currently a test key (starts with `sk_test_`)
- [ ] **0.3** Verify logging is capturing Stripe operations (check Railway logs for Stripe-related entries)
- [ ] **0.4** Test maintenance mode toggle:
  - Set `MAINTENANCE_MODE=true` in Railway env vars
  - Verify 503 responses on `/api/upload` and payment endpoints
  - Disable maintenance mode
- [ ] **0.5** Document current test Stripe dashboard state:
  - List any products/prices created
  - Note recent test sessions
  - Screenshot webhook configuration (if any)

### Phase 1: Dual-Environment Design

#### Required Environment Variables

| Variable | Test Environment | Production Environment | Notes |
|----------|------------------|------------------------|-------|
| `STRIPE_SECRET_KEY` | `sk_test_xxx...` | `sk_live_xxx...` | **REQUIRED** - From Stripe Dashboard |
| `STRIPE_WEBHOOK_SECRET` | `whsec_test_xxx...` | `whsec_live_xxx...` | For webhook signature verification |
| `STRIPE_PUBLISHABLE_KEY` | `pk_test_xxx...` | `pk_live_xxx...` | Currently unused; for future Stripe.js |

#### Recommended Deployment Strategy

**Use separate Railway environments** (not runtime toggle):

```
┌─────────────────────────────────────────────────────────────┐
│                    RECOMMENDED SETUP                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐        ┌──────────────────┐           │
│  │    STAGING       │        │   PRODUCTION     │           │
│  │   Environment    │        │   Environment    │           │
│  ├──────────────────┤        ├──────────────────┤           │
│  │ sk_test_xxx      │        │ sk_live_xxx      │           │
│  │ whsec_test_xxx   │        │ whsec_live_xxx   │           │
│  │ Test cards work  │        │ Real charges     │           │
│  │ No real money    │        │ Real money       │           │
│  └──────────────────┘        └──────────────────┘           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Why separate environments (not runtime toggle)?**
- Prevents accidental mixing of test/live data
- Clearer audit trail
- Easier rollback (switch deployment, not code)
- Industry best practice

#### Data Separation
- Stripe automatically separates test vs live customers/payments
- Campaign JSON files isolated per Railway instance
- No cross-contamination risk with separate environments

### Phase 2: Per-Property Pricing Strategy

#### Option Analysis

| Option | Description | Pros | Cons | Implementation Effort |
|--------|-------------|------|------|----------------------|
| **A: Direct Checkout** | Current model - charge per campaign | Already implemented; simple | No prepurchase option | None (current) |
| **B: Prepaid Credits** | Buy credit packs; decrement per property | User convenience; recurring revenue | Needs user accounts; credit tracking | High |
| **C: Metered Billing** | Track usage; invoice monthly | Enterprise-friendly | Complex; subscription management | Very High |

#### Recommendation: **Option A (Direct Checkout)** for Initial Launch

**Rationale**:
1. Already working and tested
2. No additional infrastructure needed
3. Simple mental model for users
4. Can add credits system later as enhancement

**Required Enhancements for Option A**:
- Add webhook handler for payment reliability
- Add idempotency to prevent duplicate campaigns
- Consider session deduplication

### Phase 3: Live Launch Checklist

#### Stripe Dashboard Setup

- [ ] **3.1** Switch to Live mode in Stripe Dashboard (toggle in left sidebar)
- [ ] **3.2** Verify business details complete:
  - Business name and address
  - Bank account for payouts
  - Tax settings (if applicable)
- [ ] **3.3** Enable payment methods:
  - Cards (Visa, Mastercard, Amex)
  - Consider: Apple Pay, Google Pay (automatic with Checkout)
- [ ] **3.4** Create webhook endpoint in Stripe Dashboard:
  - **URL**: `https://your-api-domain.com/api/stripe/webhook`
  - **Events to subscribe**:
    - `checkout.session.completed`
    - `checkout.session.expired`
    - `payment_intent.succeeded`
    - `payment_intent.payment_failed`
- [ ] **3.5** Copy webhook signing secret → save as `STRIPE_WEBHOOK_SECRET`
- [ ] **3.6** Add domain for Checkout:
  - Settings → Branding → Domains
  - Add `www.prospect-grid.com`

#### Backend Implementation Tasks (Before Deploy)

- [ ] **3.7** Implement webhook endpoint with signature verification:

```python
# Pseudocode - implementation needed
@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET")
        )
    except ValueError:
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError:
        return "Invalid signature", 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # Process payment completion
        # Create campaign if not exists
        # Log event

    return "OK", 200
```

- [ ] **3.8** Add idempotency check to prevent duplicate campaigns:
  - Check if campaign with `stripe_session_id` already exists
  - Return existing campaign_id if found

- [ ] **3.9** Add comprehensive logging:
  - Log all payment state transitions
  - Include: timestamp, session_id, stripe_session_id, amount, status
  - Use structured logging for easy querying

- [ ] **3.10** Consider persistent storage migration:
  - **Minimum**: Redis for session/campaign data
  - **Recommended**: PostgreSQL for full persistence
  - **Risk if skipped**: Data loss on Railway redeploy

#### Backend Deployment Tasks

- [ ] **3.11** Add live environment variables to Railway production:
  ```
  STRIPE_SECRET_KEY=sk_live_xxx...
  STRIPE_WEBHOOK_SECRET=whsec_xxx...
  ```
- [ ] **3.12** Verify CORS configuration allows production frontend:
  - Check `flask-cors` settings in `app.py`
  - Ensure `www.prospect-grid.com` is allowed
- [ ] **3.13** Configure Railway deployment:
  - Auto-deploy from `main` branch
  - Set appropriate instance size
- [ ] **3.14** Set up health checks:
  - Add `/health` endpoint if not exists
  - Configure Railway health check

#### QA Tasks

- [ ] **3.15** **Test mode full verification**:
  - Upload 10-property CSV
  - Complete checkout with test card `4242424242424242`
  - Verify campaign processes correctly
  - Download results

- [ ] **3.16** **Live mode smoke test**:
  - Create minimum charge campaign ($0.50)
  - Use real card
  - Verify end-to-end:
    - ✓ Checkout completes
    - ✓ Webhook received (check Stripe Dashboard)
    - ✓ Campaign created
    - ✓ Processing completes
    - ✓ Results available

- [ ] **3.17** **Failure testing**:
  - Test declined card: `4000000000000002`
  - Verify graceful error handling
  - Test insufficient funds: `4000000000009995`
  - Verify user can retry

- [ ] **3.18** **Webhook testing**:
  ```bash
  # Install Stripe CLI
  stripe listen --forward-to localhost:5000/api/stripe/webhook

  # Trigger test events
  stripe trigger checkout.session.completed
  ```

- [ ] **3.19** **Reconciliation check**:
  - Compare Stripe Dashboard payments vs campaign records
  - Verify amounts match
  - Check for orphaned payments (paid but no campaign)

### Phase 4: Rollback Plan

#### Emergency Procedures

| Scenario | Immediate Action | Steps |
|----------|------------------|-------|
| **Webhooks failing** | Disable webhook | Stripe Dashboard → Webhooks → Disable endpoint |
| **Payment flow broken** | Enable maintenance mode | Railway: Set `MAINTENANCE_MODE=true` |
| **Need to revert to test mode** | Swap API keys | Replace `STRIPE_SECRET_KEY` with `sk_test_` key |
| **Full code rollback** | Deploy previous version | Railway → Deployments → Rollback |
| **Refund needed** | Process via Dashboard | Stripe Dashboard → Payments → Select → Refund |

#### Monitoring Checklist

| What to Monitor | Where | Alert Threshold |
|-----------------|-------|-----------------|
| Webhook delivery success | Stripe Dashboard → Webhooks | < 95% success rate |
| Payment success rate | Stripe Dashboard → Payments | < 90% success rate |
| API error rate | Railway logs | Any `Stripe error` entries |
| Campaign creation rate | Application logs | Mismatch with payment count |
| Processing completion | Application logs | Campaigns stuck > 30 min |

#### Troubleshooting Runbook

| # | Failure Mode | Symptoms | Diagnostic Steps | Resolution |
|---|--------------|----------|------------------|------------|
| 1 | Invalid API key | All Stripe calls return auth error | Check Railway logs for "Invalid API Key" | Verify `STRIPE_SECRET_KEY` env var; redeploy |
| 2 | Webhook signature invalid | 400 errors on webhook endpoint | Check webhook logs in Stripe Dashboard | Verify `STRIPE_WEBHOOK_SECRET` matches Dashboard |
| 3 | Checkout session not found | `verify-payment` returns 400 | Check session_id format; check Stripe Dashboard for session | User may need to re-checkout |
| 4 | Payment success but no campaign | User stuck on processing page | Check Railway logs for verify-payment errors | Manual campaign creation or refund |
| 5 | Duplicate campaigns | Multiple campaigns for same payment | Query campaigns by stripe_session_id | Implement idempotency check |
| 6 | Campaign data lost | 404 on status/results | Railway redeployed; `/tmp` wiped | Implement webhook recovery or manual re-run; refund if needed |
| 7 | Processing thread dies | Campaign stuck at partial progress | Railway instance restarted | Manual resume processing or refund |
| 8 | Minimum price triggered unexpectedly | User charged $0.50 for tiny campaign | Expected behavior (Stripe min) | Document in UI; explain to user |
| 9 | Success URL mismatch | 404 after payment | Domain doesn't match `www.prospect-grid.com` | Update success_url in code; redeploy |
| 10 | CORS error on checkout | Frontend can't call create-checkout | Missing CORS header | Verify flask-cors config; add domain |

---

## Step 4: Admin Override Design

### Requirements

| Requirement | Description |
|-------------|-------------|
| Bypass Stripe billing | Admin can run scans without payment |
| Pay actual API costs | Google/Gemini APIs still charged (to your account) |
| No user exposure | Normal users cannot access bypass |
| Auditable | All admin usage logged with details |
| Revocable | Can disable without code deployment |

### Option 1: Server-Side Admin Token (RECOMMENDED)

#### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                     ADMIN TOKEN FLOW                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Configure env var:                                       │
│     ADMIN_TOKEN_HASH=<bcrypt hash of secret token>          │
│                                                              │
│  2. Admin sends request:                                     │
│     POST /api/admin/process-free                            │
│     Headers: X-Admin-Token: my-secret-token-123             │
│     Body: { "session_id": "xxx", "email": "admin@co.com" }  │
│                                                              │
│  3. Backend validates:                                       │
│     bcrypt.checkpw(token, ADMIN_TOKEN_HASH) == True         │
│                                                              │
│  4. If valid:                                                │
│     - Skip Stripe checkout entirely                         │
│     - Create campaign directly                              │
│     - Log: timestamp, IP, session_id, property_count        │
│     - Return campaign_id                                    │
│                                                              │
│  5. Processing runs normally (APIs still called/charged)    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### Security Measures

| Measure | Implementation |
|---------|----------------|
| Token never in frontend | Header-based, server-only endpoint |
| Bcrypt hashing | Even if env var leaked, token not revealed |
| Rate limiting | Max 5 requests/minute per IP |
| IP whitelist (optional) | Restrict to known admin IPs |
| Audit logging | Every use logged with full context |
| Rotation | Change env var to rotate; no redeploy needed |

#### What Gets Bypassed vs. What Doesn't

| Component | Bypassed? | Notes |
|-----------|-----------|-------|
| Stripe checkout | ✅ Yes | No payment collected |
| Payment verification | ✅ Yes | No Stripe session needed |
| Google Geocoding API | ❌ No | Still called; still costs money |
| Google Street View API | ❌ No | Still called; still costs money |
| Gemini AI API | ❌ No | Still called; still costs money |
| Campaign creation | ❌ No | Campaign created normally |
| Results delivery | ❌ No | Results returned normally |

#### Implementation Scope

```python
# New endpoint needed (pseudocode)
@app.route("/api/admin/process-free", methods=["POST"])
def admin_process_free():
    # 1. Validate admin token
    token = request.headers.get("X-Admin-Token")
    if not token or not verify_admin_token(token):
        return jsonify({"error": "Unauthorized"}), 401

    # 2. Rate limit check
    if is_rate_limited(request.remote_addr):
        return jsonify({"error": "Rate limited"}), 429

    # 3. Get session data
    data = request.json
    session_id = data.get("session_id")
    email = data.get("email", "admin@internal")

    # 4. Create campaign (skip Stripe)
    campaign_id = create_campaign_without_payment(session_id, email)

    # 5. Audit log
    log_admin_action(
        action="free_process",
        session_id=session_id,
        campaign_id=campaign_id,
        ip=request.remote_addr,
        property_count=get_property_count(session_id)
    )

    # 6. Start processing
    start_background_processing(campaign_id)

    return jsonify({"campaign_id": campaign_id})
```

**New Environment Variable**:
```
ADMIN_TOKEN_HASH=$2b$12$xxxxx...  # bcrypt hash
```

**Estimated Implementation**: ~50-100 lines of code

### Option 2: Admin User Role in Database

#### How It Works

```
1. Create users table with role column
2. Admin user has role='admin' in database
3. Login returns JWT with role claim
4. Checkout flow checks role; admin skips payment
5. All actions tied to authenticated user
```

#### Pros/Cons

| Pros | Cons |
|------|------|
| Standard auth pattern | Requires full auth system |
| User-tied audit trail | Needs password management |
| Role easily revocable | Significant implementation |
| Scalable to multiple admins | Overkill for single admin |

#### Implementation Scope
- New: users table, auth endpoints, password hashing, JWT middleware
- Modify: all payment-gated endpoints
- **Estimated effort**: 300-500 lines of code

### Option 3: Signed Admin JWT (Short-Lived)

#### How It Works

```
1. Admin calls: POST /api/admin/mint-token
   Body: { "password": "admin-password" }
   Returns: { "token": "eyJ...", "expires_in": 900 }

2. Within 15 minutes, admin calls:
   POST /api/admin/process-free
   Headers: Authorization: Bearer eyJ...

3. Backend validates JWT signature + expiry
4. Campaign created without payment
```

#### Pros/Cons

| Pros | Cons |
|------|------|
| Short-lived tokens | Two secrets to manage |
| Can revoke all tokens by rotating secret | More complex than Option 1 |
| Standard JWT pattern | Token minting adds step |

#### Implementation Scope
- New: mint-token endpoint, JWT validation
- New dependency: PyJWT
- **Estimated effort**: 100-150 lines of code

### Recommendation: Option 1 (Server-Side Admin Token)

| Factor | Option 1 | Option 2 | Option 3 |
|--------|----------|----------|----------|
| Implementation effort | **Low** | High | Medium |
| Architecture fit | **Best** (no auth system) | Requires new auth | Good |
| Rotation ease | **Env var refresh** | DB update | Invalidates all tokens |
| Security | **Sufficient** | Overkill | Good |
| Auditability | **Good** | Best | Good |

**Option 1 is recommended** because:
1. Minimal implementation effort
2. Fits current architecture (no user system)
3. Meets all security requirements
4. Easy to rotate credentials
5. Can upgrade to Option 2 later if needed

---

## Step 5: Open Questions / Unknowns

| # | Question | Where Searched | Resolution Needed |
|---|----------|----------------|-------------------|
| 1 | Frontend repo URL correct? | `github.com/DigitalDawnAI/prospect-grid-web` returned 404 | Verify repo exists; may be private |
| 2 | Persistent storage planned? | `storage_helper.py` comments mention PostgreSQL/Redis | Decide before live; ephemeral `/tmp` is high risk |
| 3 | Production domain correct? | Hardcoded `www.prospect-grid.com` in `app.py:239-240` | Verify matches actual frontend deployment |
| 4 | Google API keys production-ready? | `.env.example` shows placeholders | Verify live keys have quotas/billing enabled |
| 5 | Staging environment exists? | Only one Railway config found | Recommend separate staging |
| 6 | Expected transaction volume? | Not documented | Informs rate limiting decisions |
| 7 | Refund/dispute policy? | Not documented | Define before launch |
| 8 | Email notifications needed? | Campaign stores email but no sending | Consider completion emails |

---

## Appendix: Quick Reference

### API Endpoints Summary

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/upload` | POST | None | Upload CSV, get session_id |
| `/api/estimate/{session_id}` | GET | None | Get pricing estimate |
| `/api/create-checkout-session` | POST | None | Create Stripe checkout |
| `/api/verify-payment/{stripe_session_id}` | POST | Payment | Verify payment, start processing |
| `/api/status/{campaign_id}` | GET | None | Get processing progress |
| `/api/results/{campaign_id}` | GET | None | Get final results |
| `/api/stripe/webhook` | POST | Signature | **TO IMPLEMENT** |
| `/api/admin/process-free` | POST | Admin Token | **TO IMPLEMENT** |

### Environment Variables Reference

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `STRIPE_SECRET_KEY` | Yes | Stripe API authentication | `sk_live_xxx` |
| `STRIPE_WEBHOOK_SECRET` | For webhooks | Webhook signature verification | `whsec_xxx` |
| `STRIPE_PUBLISHABLE_KEY` | No (unused) | Frontend Stripe.js | `pk_live_xxx` |
| `GOOGLE_MAPS_API_KEY` | Yes | Geocoding + Street View | `AIza...` |
| `GEMINI_API_KEY` | Yes | AI scoring | `xxx` |
| `MAINTENANCE_MODE` | No | Kill switch | `true` or `false` |
| `ADMIN_TOKEN_HASH` | For admin | Admin bypass auth | `$2b$12$xxx` |

### Test Cards Reference

| Card Number | Behavior |
|-------------|----------|
| `4242424242424242` | Success |
| `4000000000000002` | Decline |
| `4000000000009995` | Insufficient funds |
| `4000000000000069` | Expired card |
| `4000000000000127` | Incorrect CVC |

### Key File Locations

| Purpose | File Path | Key Lines |
|---------|-----------|-----------|
| Stripe init | `app.py` | 14, 45 |
| Checkout creation | `app.py` | 171-256 |
| Payment verification | `app.py` | 259-347 |
| Pricing calculation | `app.py` | 214-221 |
| Campaign storage | `src/storage_helper.py` | 103-156 |
| Processing pipeline | `app.py` | 474-562 |

---

*Document generated by Claude (Opus 4.5) on 2026-02-18*
*This is a planning document only. No code changes were made.*
