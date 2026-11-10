# Stompbox Competitor Intelligence

Live AI-assisted competitor intelligence demo for Stompbox. See [CLAUDE.md](CLAUDE.md)
for the full plan, rules and phase breakdown.

**Status:** Phase 1 (project foundation) complete. Phases 2–9 not started.

## Layout

```text
backend/            FastAPI + Pydantic service
  app/
    api/            HTTP routes
    core/           settings (environment-driven)
    schemas/        Pydantic models and shared status enums
    services/       catalogue/competitor loading, domain normalisation
    main.py         app factory; loads and validates the catalogue at startup
  tests/            pytest suite
frontend/           React + Vite + TypeScript (foundation only)
data/catalogue/     Stompbox 100-product workbook (source of products AND competitors)
```

## Requirements

- Python 3.13
- Node.js 24 / npm 11

## Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
cp .env.example .env            # optional; no settings are required in Phase 1
uvicorn app.main:app --reload   # http://127.0.0.1:8000, docs at /docs
pytest
```

The app refuses to start if the workbook is missing or any row fails validation;
the error lists each offending row.

### Configuration

All settings come from environment variables or `backend/.env` (git-ignored). See
[backend/.env.example](backend/.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `CATALOGUE_PATH` | `data/catalogue/Stompbox_100_Product_Catalogue_No_Sale_Pricing.xlsx` | Catalogue workbook |
| `CATALOGUE_PRODUCTS_SHEET` | `Products` | Product sheet name |
| `CATALOGUE_COMPETITORS_SHEET` | `Competitors` | Competitor sheet name |

API keys (OpenRouter, search providers) are listed in `.env.example` but are not
used until later phases. Never commit them.

### API (Phase 1)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Status plus loaded product/competitor counts |
| GET | `/api/catalogue/products` | List products. Optional `q` (all terms must match name/brand/model/SKU/keywords), `category`, `subcategory`, `brand` (case-insensitive exact) |
| GET | `/api/catalogue/products/{product_id}` | Single product, 404 if unknown |
| POST | `/api/catalogue/products/lookup` | Body `{"product_ids": [...]}` (1–100). Returns products in request order, de-duplicated. Any unknown ID returns 404 with `missing_ids` |
| GET | `/api/catalogue/categories` | Category/subcategory product counts |
| GET | `/api/competitors` | Configured competitor domains |

Prices are serialised as decimal strings with two decimal places (`"189.99"`) to
avoid float rounding.

## Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the backend on :8000
npm run build
npm run lint
```

Currently it only shows backend connectivity. The dashboard is Phase 8.

## Data notes

- **Products:** 100 rows from the workbook's `Products` sheet, mapped 1:1 onto
  `StompboxProduct`. The research and audit columns (`verification_status`,
  `source_type`, `evidence_url`, `last_checked`, price audit notes, etc.) are kept
  verbatim under `provenance`.
- **Prices:** observed Stompbox snapshot from 2026-11-09, not live. Missing prices
  stay `null`; they are never set to 0.
- **`stompbox_sku`:** may hold WooCommerce internal IDs (`child-6768`). These are
  not manufacturer model numbers and must not be used for exact matching.
- **Competitors:** 13 rows from the workbook's `Competitors` sheet, deduplicated by
  normalised hostname (lowercase, `www.` stripped). The free-text `classification`
  is kept as-is and mapped to a `company_type` enum through an explicit table, so an
  unknown classification is a load error, not a guess. `Reference only` entries
  (Gibson) get `price_benchmark=false`.
