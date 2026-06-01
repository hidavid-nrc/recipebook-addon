# Recipe Book — Home Assistant Add-on

Personal recipe library. AI-powered search, meal planning, cook mode, and Bring! shopping list integration. Runs natively as a HA add-on on your Pi 5.

## Features

- **Catalog** — browse, tag filter, full-text + semantic (vector) search
- **Voice search** — Web Speech API, falls back to text search
- **Recipe detail** — ingredient scaling, personal notes, cook mode
- **Cook mode** — fullscreen step-by-step, ingredient checklist, live timers, screen wake lock
- **Meal planner** — weekly grid, manual or AI-generated plans
- **Bring! push** — one tap sends week's ingredients to `todo.spezialassu` in HA → syncs to Bring!
- **Preferences** — freeform text → Claude extracts taste profile, used for search ranking and meal planning
- **Gap analysis** — Claude reviews your collection and suggests missing recipes
- **Ingest** — URL scraping, canonical HTML paste, JSON upload/push, batch import

## Installation

### 1. Push to GitHub

Create a new repo (public or private), push the contents of this folder:

```bash
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/hidavid-nrc/recipebook-addon
git push -u origin main
```

Update `repository.json` with your actual GitHub username.

### 2. Add repository to Home Assistant

Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories

Add: `https://github.com/hidavid-nrc/recipebook-addon`

### 3. Install

Find "Recipe Book" in the store → Install (takes a few minutes to build on Pi 5).

### 4. Configure

In the add-on Configuration tab:

```yaml
anthropic_api_key: "sk-ant-..."
openai_api_key: "sk-..."
ha_token: "your-long-lived-access-token"
bring_entity: "todo.spezialassu"
```

**Getting a HA long-lived token:**
Profile (bottom left in HA) → Long-Lived Access Tokens → Create Token

### 5. Start

Click Start → open from HA sidebar as "Recipes".

---

## API Reference

All endpoints are under `/api/`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/recipes` | List all (add `?search=x&semantic=true` for AI search) |
| POST | `/api/recipes` | Create/update one recipe |
| GET | `/api/recipes/{slug}` | Get recipe + note |
| DELETE | `/api/recipes/{slug}` | Delete recipe |
| PUT | `/api/recipes/{slug}/note` | Save note |
| GET | `/api/recipes/embed-all` | Index all for semantic search |
| POST | `/api/ingest/url` | Scrape URL |
| POST | `/api/ingest/html` | Parse canonical HTML |
| POST | `/api/ingest/push` | Push JSON array |
| GET | `/api/planner?week_start=YYYY-MM-DD` | Get week plan |
| PUT | `/api/planner/{week}/{day}/{slot}` | Set slot |
| DELETE | `/api/planner/{week}/{day}/{slot}` | Clear slot |
| POST | `/api/planner/generate` | AI meal plan |
| POST | `/api/planner/bring` | Push to Bring! via HA |
| GET | `/api/planner/{week}/shopping-list` | Get ingredient list |
| GET | `/api/preferences` | Get preferences |
| POST | `/api/preferences` | Save + extract |
| POST | `/api/preferences/gaps` | Gap analysis |

## Claude Direct Push

From any Claude conversation or script:

```bash
POST https://ha.11082025.xyz/api/hassio/ingress/recipe_book/api/ingest/push
Content-Type: application/json

{"recipes": [{"slug": "pad-thai", "name": "Pad Thai", ...}]}
```

## Claude System Prompt (for photo → HTML → import workflow)

When photographing a recipe page and asking Claude to convert it, use this system prompt:

```
Convert this recipe to HTML using these exact CSS classes:
- article.recipe wraps each recipe
- .recipe-label for source/page reference
- .recipe-title for the recipe name
- .recipe-subtitle for subtitle
- .recipe-meta with .meta-item children for yield/time metadata
- .recipe-note for tips/substitutions
- .recipe-body containing:
  - aside.ingredients with .ingredient-group > .group-name + .ingredient > .ing-amount
  - section.steps with .step > .step-num + .step-text

Rules:
- Always use metric units (ml, g, kg, °C)
- ing-amount contains ONLY quantity + unit (e.g. "475 ml", "2.3 kg")
- Food item and prep note follow outside the ing-amount span
- Multiple recipes in one file: use multiple article.recipe elements
```

Then paste the resulting HTML into the "From HTML" tab in the app.

## Data

All data stored in `/data/recipes.db` (SQLite WAL mode). Persisted by HA Supervisor. Backed up automatically with HA snapshots.

## Local Development

```bash
cd recipebook
pip install -r backend/requirements.txt
DATA_DIR=./data \
  ANTHROPIC_API_KEY=sk-ant-... \
  OPENAI_API_KEY=sk-... \
  HA_TOKEN=... \
  BRING_ENTITY=todo.spezialassu \
  uvicorn backend.main:app --reload --port 8000
```

Open `http://localhost:8000`.
