# Shoebox Card Collector

Standalone successor project to the original Shoebox BI prototype.

## What is separate now

- New Streamlit entry point: `app.py`
- New database helper: `database.py`
- New navigation centered on set collecting rather than portfolio BI
- Uses the new normalized tables: `master_sets`, `master_cards`, `collection_copies`, `card_values`, `card_images`, `listings`
- The original Shoebox app and legacy `cards` table are not modified by this codebase

## Current backend strategy

For the first phase, this project intentionally shares the existing Supabase project so the master catalog work already completed is reused. This is a codebase separation, not yet a physical database separation. Moving these tables into a dedicated Supabase project later is straightforward because the new app reads only the normalized schema.

## Streamlit secrets

Configure either:

```toml
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="YOUR_SERVER_SIDE_KEY"
```

or the legacy `SUPABASE_KEY` name if that is what the current deployment already uses.

Never expose the service-role key to browser JavaScript or commit it to source control.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## First implemented flows

1. Set Builder with visual grid and quantity +/- controls
2. Bulk quantity editing through a data editor
3. Camera/upload/manual card entry
4. Copy-level collection records
5. Valuation display hooks for vintage condition and PSA grades
6. Master collection CSV export

## Next build targets

- Fully enrich 1983, 1984, and 1985 Topps Baseball checklists and images
- Add automatic image/card recognition
- Populate card value ladder (G/VG/EX/NM for vintage; Raw/PSA 9/10 for modern)
- Add copy-level edit drawer and sale/trade controls
- Add XLSX export generated directly from the normalized database
- eBay listing integration


## External catalog fallback

Smart Add now supports the pipeline:

1. Search local `master_cards`
2. If no match, query an external catalog provider
3. Show likely matches
4. User confirms one
5. Persist it into `master_sets` / `master_cards`
6. Add the physical copy to `collection_copies`

Configure an external provider with:

```bash
CARD_CATALOG_SEARCH_URL=https://your-provider.example/search
```

The endpoint should accept `q` and `limit` and return JSON under `results`.
Until a provider URL is configured, Smart Add will fail safely and continue to Manual Fallback rather than inventing catalog data.


## Live sports-card discovery — The Card API

Shoebox now uses **The Card API** as its default external sports-card catalog
provider when a Smart Add search is not found locally.

Create a free API key at The Card API and add this secret to the Streamlit app:

```toml
THE_CARD_API_KEY = "your_key_here"
```

The fallback flow is:

1. Search local `master_cards`
2. Query `https://www.thecardapi.com/api/v1/catalog`
3. Display likely sports-card matches
4. Confirm the correct match
5. Add the set/card once to Shoebox's master catalog
6. Add the user's physical copy

The adapter remains isolated in `external_catalog.py`, so another provider can
be added or substituted later without changing collection records.


### Verify the connection

After adding `THE_CARD_API_KEY` to Streamlit Secrets, use the sidebar button
**Test The Card API**. A successful test confirms the live Catalog API can be
used by Smart Add.

Catalog authentication uses the provider's `x-api-key` header. The separate
market-sales API uses `x-market-api-key`; Shoebox keeps those concerns isolated.


## Safety protections

- Whole-set bulk actions require an explicit confirmation checkbox.
- Large visible-set bulk changes require confirmation.
- Removing cards by marking them `Need` requires confirmation.
- Collection page includes **Delete erroneous entries** with row selection and a second confirmation step.


## Set hydration and reference images

Set Builder now includes **Load / refresh checklist & reference photos**.

- Loads 100 catalog cards at a time from The Card API.
- Caches player/card metadata into `master_cards`.
- Caches a reference image URL only when the provider returns one.
- User-uploaded copy photos remain independent and are never overwritten.
- Chunking prevents an accidental multi-thousand-record API allowance burn.

1989 Upper Deck Baseball has been added to the master-set database as an
800-card set (Low Series 1-700, High Series 701-800).


## Catalog access diagnostics

The sidebar now includes **Test Catalog Access**. It separately tests:

- `/api/v1/catalog`
- `/api/v1/catalog/sets`

HTTP errors now include the provider's response body. A 403 is surfaced as a
catalog-plan/access issue rather than a generic networking error. Clearing a
browser/PC cache does not resolve provider authorization and does not remove
images already persisted in Supabase storage.


## Cached estimated values

Shoebox now uses The Card API's free Market Sales endpoint for on-demand eBay
sold-comparison estimates.

- 1980+: Raw, PSA 9, PSA 10
- Pre-1980: Good, VG, EX, NM, PSA 8, PSA 9, PSA 10
- Results are stored in `card_values`
- Values remain visible after the API call
- Refresh is intentionally one-card-at-a-time to protect the daily sales budget
- Estimates are medians of filtered sold records, not formal appraisals

The free market plan currently provides 5,000 returned sales/day with a 3-day
lookback. This is separate from paid Catalog API access.


## Batch valuation

Set Builder now supports **Batch value refresh** with:
- Owned / visible / selected-card scope
- 7-day freshness skip by default
- Optional forced refresh
- API-budget estimate before running
- User-set maximum cards per run
- Explicit confirmation
- Per-card progress and failure handling

The default batch is intentionally small to protect the free market-sales allowance.


## Reliability v13

Batch valuation no longer performs one Supabase freshness query per card.
Freshness is derived from the valuation records already loaded for the active
set, eliminating an N+1 database-read pattern.

Market-sales requests now use:
- 30-second read timeout
- up to 4 attempts
- exponential retry/backoff
- retry handling for transient Windows socket/network failures and 5xx errors


## v14 — Paid catalog disabled by default

The Card API Catalog endpoints require paid catalog access. Shoebox now disables
catalog hydration by default to prevent repeated 403 errors.

Free market-sales pricing remains enabled.

To explicitly re-enable paid catalog access later:

```toml
ENABLE_PAID_CARD_CATALOG = "true"
```

Known sets should be maintained in Shoebox's own Supabase master catalog.


## v15 — Mobile Smart Scan

The Add / Scan Card page is now phone-first:

- Large camera-first workflow on HTTPS/Streamlit Cloud
- Gemini visual identification from the card photo
- Extracts year, sport, manufacturer, set, player, card number, variation,
  rookie flag, grader and grade when visible
- Searches Shoebox's local master catalog first
- Requires user confirmation before adding anything
- If the card/set is not known locally, the extracted fields are editable and
  Shoebox can create the set/card once, then add the photographed physical copy
- Uploaded card photo is stored in Supabase Storage
- Smart manual search remains available as the no-photo fallback
- Mobile CSS improves tap targets and spacing

The AI result is a suggestion, not a definitive card identification.


## v17 — Photo-first collection images

- Streamlit upgraded to 1.62+.
- Native camera capture requests 1080p.
- Original capture bytes are uploaded to Supabase Storage without resizing.
- User-uploaded/captured front photos are preferred over catalog/reference images.
- Reference images remain fallback-only.
- Native `st.camera_input` does not currently expose a front/rear-facing camera
  parameter; forcing the iPhone rear camera would require a custom browser
  camera component using `facingMode: environment`.
