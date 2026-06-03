# backend/routers/epub.py  — v0.4.1
# Fixes: Haiku model, bool casting, ZIP import, job-based polling, repair preserves type

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
import asyncio, zipfile, io, re, json, logging, uuid, os
from datetime import datetime

from ..db import upsert_recipe, get_recipe
from ..llm import _anthropic, _json
from .recipes import _embed

logger = logging.getLogger(__name__)
router = APIRouter()

HAIKU = "claude-haiku-4-5-20251001"

# In-memory job store {job_id: {status, log, stats}}
_jobs: dict = {}

# ── Claude with Haiku ─────────────────────────────────────────────────────────

async def haiku(system: str, user: str, max_tokens: int = 2000) -> str:
    msg = await _anthropic.messages.create(
        model=HAIKU, max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}]
    )
    return msg.content[0].text.strip()

# ── EPUB parsing ──────────────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    html = re.sub(r'<(script|style)[^>]*>[\s\S]*?</\1>', '', html, flags=re.I)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    html = re.sub(r'</(p|div|h[1-6]|li|tr)>', '\n', html, flags=re.I)
    html = re.sub(r'<[^>]+>', '', html)
    for ent, rep in [('&nbsp;',' '),('&amp;','&'),('&lt;','<'),('&gt;','>')]:
        html = html.replace(ent, rep)
    return re.sub(r'\n{3,}', '\n\n', html).strip()

def recipe_score(text: str) -> int:
    t = text.lower()
    score = 0
    if re.search(r'\b(ingredient|serves|servings|yield|makes)\b', t): score += 3
    if re.search(r'\b(tablespoon|teaspoon|tbsp|tsp|cup|ounce|pound|gram|oz\b|lb\b)\b', t): score += 3
    if re.search(r'\b(simmer|stir|cook|heat|add|chop|fry|bake|roast|velvet|marinate|brine|whisk)\b', t): score += 2
    if re.search(r'\d+\s*(?:cup|tbsp|tsp|oz|lb|g\b|ml|min|hour)', t): score += 3
    return score

def parse_epub(epub_bytes: bytes, chunk_size: int = 6000, min_score: int = 2) -> list[dict]:
    chunks = []
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zf:
        html_files = sorted([
            n for n in zf.namelist()
            if re.search(r'\.(html|xhtml|htm)$', n, re.I) and not zf.getinfo(n).is_dir()
        ])
        for fname in html_files:
            try:
                content = zf.read(fname).decode('utf-8', errors='replace')
            except Exception:
                continue
            text = html_to_text(content)
            if len(text.strip()) < 100:
                continue
            lines = text.split('\n')
            current, current_title = '', fname.split('/')[-1]
            for line in lines:
                t = line.strip()
                is_heading = (
                    3 < len(t) < 80 and (
                        re.match(r'^[A-Z][A-Z\s\(\)\-\/&]{3,}$', t) or
                        (re.match(r'^[A-Z][a-z]', t) and len(t.split()) <= 8 and not t.endswith('.'))
                    )
                )
                if is_heading and len(current) > 800:
                    if recipe_score(current) >= min_score:
                        chunks.append({'title': current_title, 'text': current.strip(), 'source': fname})
                    current = t + '\n'
                    current_title = t
                else:
                    current += line + '\n'
                    if len(current) > chunk_size:
                        if recipe_score(current) >= min_score:
                            chunks.append({'title': current_title, 'text': current.strip(), 'source': fname})
                        current = ''
            if len(current.strip()) > 800 and recipe_score(current) >= min_score:
                chunks.append({'title': current_title, 'text': current.strip(), 'source': fname})
    return chunks

# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACT_SYS = """You are a recipe extraction assistant for a cookbook. Extract ALL recipes, techniques, sauces and marinades.

Output ONLY a JSON array. Each object:
- name: string
- type: "recipe" | "technique" | "sauce" | "marinade"
- description: string (one sentence)
- yield: string e.g. "Serves 4"
- totalTime: ISO 8601 e.g. "PT30M" or null
- prepTime: ISO 8601 or null
- ingredientGroups: [{name: null, ingredients: [{raw: "2 tablespoons soy sauce", quantity: 2.0, unit: "tablespoon", food: "soy sauce", note: null, display: "2 tablespoons soy sauce"}]}]
- instructions: [{step: 1, text: "...", timer: null}]
- tags: array of strings

Rules: Extract inline quantities from steps if no ingredient list. Include techniques (velveting, brining, stocks). Return [] only for non-recipe text. Return ONLY JSON array, no markdown."""

REPAIR_SYS = """Repair an incomplete recipe extraction. Return the COMPLETE recipe with:
- name, type (MUST preserve original type: recipe/technique/sauce/marinade), description, yield, totalTime, prepTime, tags
- ingredientGroups: [{name, ingredients: [{raw, quantity, unit, food, note, display}]}]
- instructions: [{step, text, timer}] — ALL steps

Return ONLY a JSON array. No markdown."""

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_partial(r: dict) -> bool:
    ings = sum(len(g.get('ingredients', [])) for g in r.get('ingredientGroups', []))
    steps = len(r.get('instructions', []))
    return ings < 2 or steps < 1

def make_slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9\s-]', '', name.lower())
    s = re.sub(r'[\s-]+', '-', s).strip('-')
    return s[:80]

def normalize_recipe(r: dict, source: str) -> dict:
    rtype = r.get('type', 'recipe')
    tags = list(r.get('tags', []))
    if rtype not in tags:
        tags.append(rtype)
    return {
        'slug': make_slug(r.get('name', 'recipe')),
        'name': r.get('name', ''),
        'description': r.get('description', ''),
        'source': source,
        'subtitle': rtype,
        'yield': r.get('yield') or r.get('recipeYield', ''),
        'totalTime': r.get('totalTime'),
        'prepTime': r.get('prepTime'),
        'tags': tags,
        'ingredientGroups': r.get('ingredientGroups', []),
        'instructions': r.get('instructions', []),
        'note': f"Extracted from EPUB. Type: {rtype}.",
    }

def log_job(job_id: str, msg: str, level: str = 'info'):
    if job_id not in _jobs:
        return
    _jobs[job_id]['log'].append({'msg': msg, 'level': level, 'ts': datetime.utcnow().isoformat()})
    logger.info(f"[{job_id}] {msg}")

# ── Background extraction task ────────────────────────────────────────────────

async def _run_epub_import(job_id: str, epub_bytes: bytes, chunk_size: int,
                            min_score: int, max_chunks: int, do_repair: bool, source: str, bg: BackgroundTasks):
    job = _jobs[job_id]
    job['status'] = 'running'

    try:
        log_job(job_id, f"Parsing EPUB...")
        chunks = parse_epub(epub_bytes, chunk_size=chunk_size, min_score=min_score)
        total = min(len(chunks), max_chunks)
        log_job(job_id, f"Found {len(chunks)} candidate chunks, processing {total}")
        job['stats']['total_chunks'] = total

        extracted = []
        dedup = set()

        for i, chunk in enumerate(chunks[:max_chunks]):
            log_job(job_id, f"[{i+1}/{total}] {chunk['title'][:50]}...")
            job['stats']['chunk'] = i + 1
            try:
                raw = await haiku(EXTRACT_SYS, f"Extract recipes from this cookbook text:\n\n{chunk['text']}")
                recipes = _json(raw)
                if not isinstance(recipes, list):
                    recipes = []
                new_recipes = []
                for r in recipes:
                    if not r.get('name'):
                        continue
                    slug = make_slug(r['name'])
                    if slug in dedup:
                        continue
                    dedup.add(slug)
                    extracted.append((r, chunk['text']))
                    new_recipes.append(r.get('name', '?'))
                if new_recipes:
                    log_job(job_id, f"  ✓ {len(new_recipes)}: {', '.join(new_recipes)[:80]}", 'ok')
            except Exception as e:
                log_job(job_id, f"  ✗ {str(e)[:80]}", 'error')
            await asyncio.sleep(1.0)  # Haiku rate limit

        log_job(job_id, f"Extraction done: {len(extracted)} recipes")
        job['stats']['extracted'] = len(extracted)

        # Repair pass
        if do_repair:
            partials = [(r, text) for r, text in extracted if is_partial(r)]
            log_job(job_id, f"Repairing {len(partials)} partial recipes...")
            job['stats']['partials'] = len(partials)
            for i, (r, text) in enumerate(partials):
                log_job(job_id, f"[{i+1}/{len(partials)}] Repairing: {r.get('name','?')[:50]}")
                try:
                    original_type = r.get('type', 'recipe')
                    raw = await haiku(REPAIR_SYS, f"Original type: {original_type}\n\nCookbook text:\n\n{text}")
                    repaired = _json(raw)
                    if isinstance(repaired, list) and repaired:
                        fixed = repaired[0]
                        # Preserve original type if repair lost it
                        if not fixed.get('type'):
                            fixed['type'] = original_type
                        merged = {**r, **fixed}
                        for j, (orig, orig_text) in enumerate(extracted):
                            if orig.get('name') == r.get('name'):
                                extracted[j] = (merged, orig_text)
                                break
                        ings = sum(len(g.get('ingredients',[])) for g in fixed.get('ingredientGroups',[]))
                        steps = len(fixed.get('instructions', []))
                        log_job(job_id, f"  ✓ {ings} ing, {steps} steps", 'ok')
                except Exception as e:
                    log_job(job_id, f"  ✗ {str(e)[:60]}", 'error')
                await asyncio.sleep(1.0)

        # Import to DB
        imported, skipped = 0, 0
        for r, _ in extracted:
            normalized = normalize_recipe(r, source)
            existing = get_recipe(normalized['slug'])
            if existing:
                skipped += 1
                continue
            upsert_recipe(normalized)
            bg.add_task(_embed, normalized)
            imported += 1

        job['stats']['imported'] = imported
        job['stats']['skipped'] = skipped
        log_job(job_id, f"Done! Imported: {imported} | Skipped (duplicates): {skipped} | Total: {len(extracted)}", 'done')
        job['status'] = 'done'

    except Exception as e:
        log_job(job_id, f"Fatal error: {str(e)}", 'error')
        job['status'] = 'error'

# ── ZIP JSON import (for existing extracted ZIPs) ─────────────────────────────

@router.post("/zip")
async def import_zip(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    source: str = Form("ZIP Import"),
):
    """Import a ZIP of JSON recipe files (from the browser extractor tool)."""
    zip_bytes = await file.read()
    imported, skipped, errors = 0, 0, 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            json_files = [n for n in zf.namelist() if n.endswith('.json')]
            for fname in json_files:
                try:
                    r = json.loads(zf.read(fname))
                    if not r.get('name'):
                        continue
                    # Convert from extractor format to Recipe Book format
                    normalized = {
                        'slug': make_slug(r.get('name', 'recipe')),
                        'name': r.get('name', ''),
                        'description': r.get('description', ''),
                        'source': r.get('source') or source,
                        'subtitle': r.get('recipeCategory', ['recipe'])[0] if r.get('recipeCategory') else 'recipe',
                        'yield': r.get('recipeYield', ''),
                        'totalTime': r.get('totalTime'),
                        'prepTime': r.get('prepTime'),
                        'tags': r.get('tags', []),
                        'ingredientGroups': [{
                            'name': None,
                            'ingredients': [
                                _norm_ing(ing) for ing in r.get('recipeIngredient', [])
                            ]
                        }],
                        'instructions': [
                            {'step': i+1, 'text': s.get('text','') if isinstance(s,dict) else str(s), 'timer': None}
                            for i, s in enumerate(r.get('recipeInstructions', []))
                        ],
                        'note': r.get('notes', [{}])[0].get('text','') if r.get('notes') else '',
                    }
                    # Quality gate — skip truly empty recipes
                    ings = len(normalized['ingredientGroups'][0]['ingredients'])
                    steps = len(normalized['instructions'])
                    if ings < 2 and steps < 1:
                        errors += 1
                        continue
                    existing = get_recipe(normalized['slug'])
                    if existing:
                        skipped += 1
                        continue
                    upsert_recipe(normalized)
                    bg.add_task(_embed, normalized)
                    imported += 1
                except Exception:
                    errors += 1
    except Exception as e:
        raise HTTPException(422, f"ZIP read failed: {e}")

    return {'imported': imported, 'skipped': skipped, 'errors': errors, 'total': imported + skipped + errors}

def _norm_ing(ing) -> dict:
    if isinstance(ing, str):
        return {'raw': ing, 'display': ing, 'food': ing, 'quantity': None, 'unit': None, 'note': None}
    food = ing.get('food', '')
    if isinstance(food, dict): food = food.get('name', '')
    unit = ing.get('unit', '')
    if isinstance(unit, dict): unit = unit.get('abbreviation','') or unit.get('name','')
    return {
        'raw': ing.get('raw', '') or f"{ing.get('quantity','')} {unit} {food}".strip(),
        'display': ing.get('display', '') or food,
        'food': food,
        'quantity': ing.get('quantity'),
        'unit': unit or None,
        'note': ing.get('note'),
    }

# ── EPUB endpoint — job-based polling ────────────────────────────────────────

@router.post("/epub")
async def import_epub(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_size: int = Form(6000),
    min_score: int = Form(2),
    max_chunks: int = Form(500),
    do_repair: str = Form('true'),   # string, cast explicitly
    source: str = Form("EPUB Import"),
):
    """Start an EPUB import job. Returns job_id. Poll /api/ingest/epub/{job_id} for progress."""
    epub_bytes = await file.read()
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        'status': 'queued',
        'filename': file.filename,
        'log': [],
        'stats': {'chunk': 0, 'total_chunks': 0, 'extracted': 0, 'imported': 0, 'skipped': 0},
        'created': datetime.utcnow().isoformat(),
    }
    repair = do_repair.lower() in ('true', '1', 'yes')
    bg.add_task(_run_epub_import, job_id, epub_bytes, chunk_size, min_score, max_chunks, repair, source, bg)
    return {'job_id': job_id, 'status': 'queued'}

@router.get("/epub/{job_id}")
async def epub_job_status(job_id: str, since: int = 0):
    """Poll import job status. Returns log lines since offset `since`."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    log = job['log']
    return {
        'job_id': job_id,
        'status': job['status'],
        'filename': job.get('filename', ''),
        'stats': job['stats'],
        'log': log[since:],
        'log_total': len(log),
    }
# cache-bust: 2026-06-03
