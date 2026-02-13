# Investigation: Campaign Serialization (Campaigns Running One-at-a-Time)

**Date:** 2026-02-13
**Commit under review:** `1533ca0f96e380d488ca38d36bb3a54f57878226`
**Symptom:** Campaign #2 does not start until Campaign #1 completes.

---

## 1. What Changed in Commit `1533ca0`

**File modified:** `app.py` — 3 additions, 3 deletions.

### Change A: `job_timeout=7200` added to both enqueue call sites

- `app.py:513` (Stripe webhook handler):
  ```python
  queue.enqueue(process_campaign, campaign_id, job_timeout=7200)
  ```
- `app.py:659` (`start_processing` endpoint):
  ```python
  queue.enqueue(process_campaign, campaign_id, job_timeout=7200)
  ```

**Impact:** RQ default timeout is 180s. With 500 properties at ~4s Gemini throttle each, a campaign takes ~33 minutes. The old timeout was killing jobs. The 2-hour timeout prevents that. **This does not affect concurrency** — it only prevents premature job death. A long-running job holding a single worker for up to 2 hours actually *worsens* serialization.

### Change B: `PROCESSING_WORKERS` default reduced from 10 to 3

- `app.py:703`:
  ```python
  PROCESSING_WORKERS = int(os.getenv("PROCESSING_WORKERS", "3"))
  ```

**Impact:** Controls `ThreadPoolExecutor(max_workers=N)` at `app.py:836` for parallel **property** processing within a single campaign. **Does NOT control campaign-level concurrency.** Reducing 10→3 lowers memory per campaign but has zero effect on campaign parallelism.

**Summary:** Neither change addresses campaign-level concurrency.

---

## 2. Current Concurrency Model (As Implemented)

```
┌──────────────────────────────────────────────────────────────────┐
│                         Railway Platform                         │
│                                                                  │
│  ┌─────────────────────┐        ┌──────────────────────────────┐│
│  │   "web" service      │        │   "worker" service           ││
│  │   gunicorn app:app   │        │   python worker.py           ││
│  │   numReplicas: 1     │        │   numReplicas: 1 (ASSUMED)   ││
│  │                      │        │                              ││
│  │  POST /api/process   │        │  Worker([Queue("default")])  ││
│  │  POST /api/stripe-wh │        │       │                      ││
│  │       │              │        │       ▼                      ││
│  │       ▼              │        │  Picks ONE job at a time     ││
│  │  queue.enqueue(      │───────▶│  from "default" queue        ││
│  │    process_campaign, │  Redis │       │                      ││
│  │    campaign_id,      │ Queue  │       ▼                      ││
│  │    job_timeout=7200) │        │  process_campaign(id)        ││
│  │                      │        │       │                      ││
│  └─────────────────────┘        │       ▼                      ││
│                                  │  ThreadPoolExecutor           ││
│                                  │  (max_workers=3)             ││
│                                  │   ├─ Thread 1: property A    ││
│                                  │   ├─ Thread 2: property B    ││
│                                  │   └─ Thread 3: property C    ││
│                                  │       │                      ││
│                                  │       ▼                      ││
│                                  │  geocode → streetview →      ││
│                                  │  gemini_score (throttled     ││
│                                  │  via Redis: 1 call/4s)       ││
│                                  │       │                      ││
│                                  │       ▼                      ││
│                                  │  DB commit per property      ││
│                                  │  campaign.status="completed" ││
│                                  │  send_results_email()        ││
│                                  └──────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### Concurrency Summary

| Layer | What's Parallelized | Mechanism | Evidence |
|-------|-------------------|-----------|----------|
| **Campaign-level** | **Nothing** — one at a time | Single RQ worker process | `worker.py:16` |
| **Property-level** | Up to 3 concurrent | `ThreadPoolExecutor(max_workers=3)` | `app.py:836` |
| **Gemini API** | Globally serialized, 1 call per 4s | Redis `SET NX PX` throttle | `gemini_scorer.py:235-247` |

---

## 3. Ranked Hypotheses + Evidence + Validation

### A. Single RQ Worker Process (~95% confidence — MOST LIKELY)

**Evidence:**

- `worker.py:16`: `Worker([queue], connection=conn).work()` — starts ONE worker, blocking loop: pick job → execute → pick next.
- `Procfile:2`: `worker: python worker.py` — single process type.
- `railway.json:7`: `"numReplicas": 1`

**Why this causes the symptom:** Campaign #1 is picked up by the sole worker. Campaign #2 sits in the Redis `default` queue. The worker won't dequeue Campaign #2 until `process_campaign()` for Campaign #1 returns (up to 2 hours).

**Validation:**
1. Railway Dashboard → Worker service → check instance count (expect: 1)
2. Worker logs: count `Worker rq:worker:*: started` lines
3. Redis: `LLEN rq:queue:default` while Campaign #1 runs (expect: ≥1 if Campaign #2 is queued)
4. Redis: `SCARD rq:workers` (expect: 1)

---

### B. Queue / Job Configuration — Correct but Irrelevant to Parallelism

**Evidence:**

- Queue name is `"default"` in both `app.py:61` and `worker.py:15` — consistent.
- No explicit campaign-level locking found (no `redis.lock()`, `SETNX`, `campaign_lock`).
- Only gating: `app.py:801-802` checks `campaign.status == "completed"` to prevent re-processing — does NOT block concurrent campaigns.
- `MAINTENANCE_MODE` blocks API endpoints, not worker processing.

**Conclusion:** Queue config is correct. The bottleneck is worker count, not queue setup.

---

### C. Application-Level Serialization Inside `process_campaign` — None Found

**Evidence:**

- `process_campaign` (`app.py:787-886`) has NO global lock, no campaign-count check, no "only one at a time" guard.
- Gemini throttle (`gemini_scorer.py:237`) is a rate limiter, not a mutex. Multiple campaigns would share it correctly.
- No Selenium/browser — pipeline is stateless HTTP calls (geocode → Street View → Gemini API).
- `_process_single_property` is documented as thread-safe (`app.py:709`).

**Conclusion:** If two workers ran `process_campaign` simultaneously, both would work correctly.

---

### D. Infrastructure/Resource Constraints (Railway) — ASSUMPTION-HEAVY

**Possible sub-causes:**
- D1: Worker service scaled to 1 replica (confirmed by `railway.json`, but may be overridden in UI)
- D2: Worker OOM-crashes under load; restart policy masks the issue
- D3: Worker service was never created (Railway requires manual service creation from Procfile process types)
- D4: Railway plan limits total instances

**Validation:** See Section 5.

---

## 4. Repository Inspection Checklist

| # | What to Check | File / Pattern | Finding |
|---|--------------|----------------|---------|
| 1 | RQ worker boot | `worker.py:16` | `Worker([queue]).work()` — single process, no concurrency param |
| 2 | Deployment entrypoints | `Procfile` | `web: gunicorn app:app` / `worker: python worker.py` |
| 3 | Railway config | `railway.json` | `numReplicas: 1` |
| 4 | Queue naming | `app.py:61`, `worker.py:15` | Both `"default"` |
| 5 | Dockerfiles | `**/Dockerfile` | None — Nixpacks only |
| 6 | Explicit locking | `grep: lock, SETNX, mutex` | Only Gemini rate limiter (`gemini_scorer.py:237`) and `threading.Lock` fallback |
| 7 | Status gating before enqueue | `app.py:513, 659` | No "is another campaign running" check |
| 8 | Status gating inside processing | `app.py:801-802` | Only `completed` skip — no concurrent-campaign block |
| 9 | `PROCESSING_WORKERS` usage | `app.py:703, 836` | Thread pool for properties, not campaigns |
| 10 | Other worker scripts | `**/worker*` | Only `worker.py` |
| 11 | Process managers | `grep: supervisor, celery` | None |
| 12 | Gemini throttle | `gemini_scorer.py:237` | Redis-distributed rate limiter, key `gemini:call_throttle` |

---

## 5. Railway-Side Validation Steps

### Step 1: Confirm Worker Service Exists
- Railway Dashboard → Project → Services
- Verify a separate **"worker"** service exists (not just "web")
- ASSUMPTION: Worker service was created. If only "web" exists, no background processing is happening via RQ.

### Step 2: Check Worker Instance Count
- Railway Dashboard → Worker service → Deployments → Active
- Check "Instances" / "Replicas" — expect 1
- Check if autoscaling is enabled

### Step 3: Check Worker Logs
- Railway Dashboard → Worker service → Logs
- Look for: `Worker rq:worker:<id>: started, version X.Y.Z`
- Count unique worker IDs
- Look for crash traces or `Warm shut down` messages

### Step 4: Check Worker Start Command
- Railway Dashboard → Worker service → Settings → Start Command
- Must be `python worker.py` (not `gunicorn app:app`)

### Step 5: Verify Environment Variables on Worker Service

| Env Var | Purpose | Required |
|---------|---------|----------|
| `REDIS_URL` | Queue connection | Yes |
| `DATABASE_URL` | PostgreSQL | Yes |
| `GEMINI_API_KEY` | AI scoring | Yes |
| `PROCESSING_WORKERS` | Property parallelism | No (default: 3) |
| `GEMINI_RPM` | API rate limit | No (default: 15) |
| `MAINTENANCE_MODE` | Should NOT be `true` | No |

### Step 6: Check Redis Queue State (During Active Campaign)

```bash
redis-cli -u $REDIS_URL
> LLEN rq:queue:default          # Queued jobs waiting
> SMEMBERS rq:workers            # Active workers
> SCARD rq:workers               # Count of active workers
```

If `LLEN > 0` while one campaign runs → confirms jobs are queued behind the single worker.

### Step 7: Monitor Memory/CPU
- Railway Dashboard → Worker service → Metrics
- Check for memory spikes near Railway limits
- Check for OOM kills or restart loops

---

## 6. Recommended Fix Paths (Planning Only)

### Fix Path 1: Scale Worker Replicas (RECOMMENDED — Zero Code Changes)

**What:** Scale the worker service to 2-3 replicas in Railway.

**How:** Railway Dashboard → Worker service → Settings → Replicas → set to 2 or 3.
Or update `railway.json` to `"numReplicas": 2`.

**Why it works:** Each replica runs `python worker.py`, each independently dequeues jobs from `"default"`. Two replicas = two concurrent campaigns.

**Safety:** The code already handles this:
- Gemini throttle is Redis-distributed (cross-worker safe) — `gemini_scorer.py:235-247`
- DB commits are per-property, no long transactions — `app.py:877`
- No shared in-memory state between campaigns
- `_process_single_property` is documented thread-safe — `app.py:709`

**Trade-offs:**
- Each worker spawns 3 property threads, so 2 workers = 6 threads competing for Gemini API (still rate-limited to 15 RPM globally)
- Additional Railway compute cost per replica
- Two campaigns of 500 properties each still take ~66 min total (Gemini-bound) but progress concurrently

### Fix Path 2: Run Multiple Workers in One Container

**What:** Modify `worker.py` to fork N worker processes.

**How (conceptually):** Use `multiprocessing` or RQ's `--count` flag to spawn multiple worker processes within one container.

**Trade-offs:** More complex than Fix Path 1, same resource cost, single point of failure.

### Fix Path 3: Not Recommended — Per-Campaign Queues

Over-engineered. The existing shared `"default"` queue + N workers is the standard RQ pattern.

---

## 7. Key Unknowns / Assumptions

| # | Assumption | Confidence | Validation Step |
|---|-----------|------------|-----------------|
| 1 | Worker service is deployed as a separate Railway service | Medium | Check Railway service list |
| 2 | Worker runs with 1 replica | High (`railway.json`) | Check Railway worker instances |
| 3 | `railway.json` applies to worker service | Medium | Check Railway per-service config |
| 4 | Worker is not crashing during processing | Medium | Check worker logs for crashes |
| 5 | `REDIS_URL` is same on web and worker | High | Check env vars on both services |
| 6 | Gemini 15 RPM is the throughput bottleneck | High (500 × 4s = 33 min) | Check `GEMINI_RPM` env var |
| 7 | No Railway-level job scheduling overrides | High | Inspect Railway service settings |

---

## Root Cause Summary

**The single RQ worker process (`worker.py:16`) is the root cause.** RQ workers are single-threaded job processors — they pick one job, execute it to completion, then pick the next. With `job_timeout=7200`, a single campaign can hold the worker for up to 2 hours. Any campaigns enqueued during that time wait in the Redis queue.

The `PROCESSING_WORKERS=3` setting only parallelizes properties *within* a campaign, not campaigns themselves. The commit `1533ca0` correctly fixed job timeouts but did not address campaign-level concurrency because that requires multiple worker processes, not configuration changes within the existing single worker.
