# CSV Address Column Fix — Investigation & Plan

## A) Where the Export Is Generated

### Finding: No CSV export endpoint exists in this backend repo

The CSV export is **not implemented** in the Prospect-Grid-v1 backend. Both documentation files confirm it is a TODO:

- `README.md:205` — `[ ] Add CSV export endpoint`
- `claude.md:584` — `[ ] Implement CSV export endpoint`

There is no `csv.DictWriter`, `Content-Disposition`, download route, or any export logic anywhere in the backend codebase.

### Where the export actually happens

The frontend (deployed on **Vercel**, per `claude.md:571`) is a separate React/Next.js application (**not in this repo**). It fetches results via:

```
GET /api/results/<campaign_id>
```

This endpoint (`app.py:916-939`) returns a JSON payload with a `properties` array. Each property is a serialized `ScoredProperty` object containing **both** `address_full` and `address_street` fields.

**The CSV export is constructed client-side in the frontend.** The frontend JavaScript takes the JSON properties array and builds the CSV in the browser (likely using `Blob` + `createObjectURL` or a library like `papaparse`/`json2csv`).

### ASSUMPTION (requires validation)

> The frontend maps `address_full` → Column A ("Address") when building the CSV.

**Validation step:** Inspect the frontend repo for CSV export logic — search for `address_full`, `csv`, `Blob`, `download`, `export` in the Vercel-deployed frontend codebase.

---

## B) Why Column A Contains "street, city, state zip, USA"

### Root cause chain (fully traced)

| Step | File:Line | What happens |
|------|-----------|--------------|
| 1 | `geocoder.py:187` | Google Maps API returns `formatted_address` (e.g., `"701 Gull Wing Ct, Galloway, NJ 08205, USA"`) |
| 2 | `geocoder.py:209-210` | `address_full=formatted_address` — the raw Google string is stored verbatim |
| 3 | `geocoder.py:190-192` | `address_street` is parsed separately: `street_number + route` → `"701 Gull Wing Ct"` |
| 4 | `models.py:168-169` | `ScoredProperty` stores both `address_full` and `address_street` as separate fields |
| 5 | `app.py:747` | `prop.model_dump(mode="json")` serializes ALL fields including both address fields |
| 6 | `app.py:916-939` | `/api/results/<campaign_id>` returns the full JSON with both fields |
| 7 | **Frontend** (not in repo) | CSV export maps `address_full` → Column A instead of `address_street` |

### Evidence for each claim

**`formatted_address` from Google includes ", USA":**
- `geocoder.py:187`: `formatted_address = result["formatted_address"]`
- Google Maps Geocoding API always appends the country name to `formatted_address` for US addresses.

**`address_full` stores the Google `formatted_address` verbatim:**
- `geocoder.py:209-210`: `address_full=formatted_address`

**`address_street` already stores street-only:**
- `geocoder.py:190-192`:
  ```python
  street_number = components.get("street_number", "")
  route = components.get("route", "")
  street = f"{street_number} {route}".strip()
  ```
- `geocoder.py:211`: `address_street=street`

**State uses `long_name` (full state name, e.g., "New Jersey"):**
- `geocoder.py:182`: `comp["types"][0]: comp["long_name"]` — the component dict is built using `long_name` for ALL components
- `geocoder.py:201`: `state = components.get("administrative_area_level_1", "")`
- This means `state` = `"New Jersey"` (not `"NJ"`). The `"NJ"` seen in the screenshot comes from the `formatted_address` string, not from the structured `state` field.

**IMPORTANT SIDE NOTE ON STATE FORMAT:**
The requirement says "Column C: State (e.g., 'New Jersey' or 'NJ' — keep whatever the system already uses consistently)." The backend stores `long_name` → `"New Jersey"`. If the current CSV shows `"NJ"`, that's because it's extracting from `address_full` / `formatted_address`. Switching to the structured `state` field will change the CSV from `"NJ"` to `"New Jersey"`. This needs a product decision. If `"NJ"` is preferred, we'd need to also extract `short_name` for the state.

---

## C) Recommended Fix Plan (Ranked)

### Option 1 (RECOMMENDED): Add a backend CSV export endpoint that uses structured fields

**What to change:**
Add a new endpoint `GET /api/export/<campaign_id>` in `app.py` that:
1. Loads the campaign properties (reuse `_load_campaign_payload`)
2. Builds CSV rows using the already-existing structured fields:
   - Column A "Address" → `address_street` (NOT `address_full`)
   - Column B "City" → `city`
   - Column C "State" → `state`
   - Column D "ZIP" → `zip`
   - Column E "Score" → `property_score` (or `prospect_score` for legacy)
   - Column F "Confidence" → `confidence_level` (or `confidence`)
   - Column G "Status" → `processing_status`
   - Column H "Street View URL" → `streetview_url`
   - Column I "Reasoning" → `score_reasoning` (or `brief_reasoning`)
3. Returns with `Content-Disposition: attachment; filename=results.csv`
4. No "USA" anywhere because structured fields never contain it

**Files touched:** `app.py` only (add ~30 lines)

**Why it's safest:**
- All structured fields (`address_street`, `city`, `state`, `zip`) already exist and are populated by the geocoder
- No parsing or string manipulation needed
- Frontend can switch to hitting the new endpoint instead of building CSV client-side
- Backend controls the format — no drift between frontend implementations

**Edge cases:**
| Case | Behavior |
|------|----------|
| Street address with comma (e.g., "Apt 2, 123 Main St") | Safe — `address_street` comes from Google's `street_number` + `route` components, which don't include unit/apt. Units from the original input would be in `address` (raw input) but NOT in the geocoded `address_street`. If unit info is needed, would require additional parsing. |
| Missing city/state/zip | Fields default to `""` (empty string) per `geocoder.py:194-202`. CSV cell will be empty. |
| PO Boxes / Rural Routes | Google may not return `street_number`/`route` components. `address_street` could be empty or partial. Fallback: use `address_full` with post-processing only for these edge cases. |
| Non-US addresses | System is US-focused (NJ addresses in test data). `formatted_address` could have non-US country names. Structured fields would still work correctly — they simply won't have "USA" in them. |
| Failed geocoding | Properties with `status: "failed"` have no geocoded data. Use `input_address` as fallback for Column A. |

**State format decision needed:** If "NJ" (abbreviation) is preferred over "New Jersey", change `geocoder.py:182` to use `short_name` for `administrative_area_level_1` specifically, or add a separate `state_abbr` field.

---

### Option 2: Fix the frontend CSV export to use `address_street` instead of `address_full`

**What to change:**
In the frontend repo (not in this backend repo), find the CSV construction logic and change the field mapping from `address_full` → `address_street` for Column A.

**Files touched:** Frontend repo only (likely 1 file, ~1 line change)

**Why it's good:**
- Minimal change — literally swapping one field name
- The `address_street` field is already present in every API response
- No backend changes needed

**Why it's riskier than Option 1:**
- Frontend CSV construction is not in this repo; requires locating and modifying the frontend
- Frontend-generated CSVs are harder to control/test centrally
- Still need a separate step to strip "USA" if `address_full` is used anywhere else in the frontend

**Edge cases:** Same as Option 1 — the data is identical, only the export location differs.

---

### Option 3: Sanitize `address_full` at geocoding time

**What to change:**
In `geocoder.py:209`, strip ", USA" from `formatted_address` before storing:
```python
address_full=formatted_address.replace(", USA", "")
```

AND in the frontend/backend export, parse `address_full` to extract street-only by splitting on the first comma.

**Files touched:** `geocoder.py` (1 line) + frontend or backend export

**Why it's the WORST option:**
- Modifying `address_full` breaks its original purpose (it's used for geocoding cache keys and Street View lookups)
- String parsing of `address_full` is fragile (commas in street names, varying formats)
- Doesn't solve the core problem — structured fields already exist
- Affects cached data vs. new data inconsistently

**DO NOT USE THIS OPTION** unless Options 1 and 2 are impossible.

---

## D) Immediate No-Code Cleanup for Existing CSVs

### Google Sheets / Excel Formula Approach

To extract street-only from Column A (which currently has "701 Gull Wing Ct, Galloway, NJ 08205, USA"):

**Google Sheets:**
```
=LEFT(A2, FIND(",", A2) - 1)
```
This extracts everything before the first comma.

To remove ", USA" from any cell:
```
=SUBSTITUTE(A2, ", USA", "")
```

**Excel:**
```
=LEFT(A2, FIND(",", A2) - 1)
```

### Limitations

| Limitation | Impact |
|------------|--------|
| Street addresses containing commas (e.g., "Apt 2, 123 Main St") | Formula would truncate at "Apt 2" — incorrect. These are rare with geocoded addresses since Google typically reformats to "123 Main St Apt 2" but not guaranteed. |
| Missing commas (failed geocoding, partial addresses) | `FIND(",")` returns `#VALUE!` error. Wrap in `IFERROR()`: `=IFERROR(LEFT(A2, FIND(",",A2)-1), A2)` |
| Non-standard Google formatting | Some addresses may have different comma placement. Manual review needed for ~1-2% of rows. |

### Optional: Tiny post-processing script concept (described only)

A Python one-liner that reads the exported CSV, replaces Column A with everything-before-first-comma, and removes ", USA" everywhere:

```
# Concept only — NOT to be run
import csv
# Read CSV → for each row: row["Address"] = row["Address"].split(",")[0]
# Also: for all cells, replace ", USA" with ""
# Write to new CSV
```

This has the same comma-in-street-name limitation as the spreadsheet approach.

---

## Summary

| Item | Key Finding |
|------|-------------|
| **Where is the CSV export?** | NOT in this repo. It's in the frontend (Vercel/Next.js, separate repo). Backend has it as a TODO. |
| **Why does Column A have full address + USA?** | Column A maps to `address_full` which is Google's `formatted_address` (includes city/state/zip/USA). Source: `geocoder.py:187,209` |
| **Do structured fields already exist?** | YES. `address_street`, `city`, `state`, `zip` are all separate fields in `ScoredProperty` (`models.py:168-172`) and populated by the geocoder (`geocoder.py:190-211`) |
| **Best fix** | Option 1: Add backend CSV export endpoint using structured fields (~30 lines in `app.py`). OR Option 2: Fix frontend to use `address_street` instead of `address_full` (~1 line change in frontend repo). |
| **State format caveat** | Backend stores `long_name` ("New Jersey"), not abbreviation ("NJ"). Product decision needed if abbreviation is preferred. |
