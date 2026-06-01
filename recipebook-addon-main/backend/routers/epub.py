# backend/routers/epub.py
# Server-side EPUB → Recipe importer
# Drop-in addition to the existing ingest router

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Optional
import asyncio, zipfile, io, re, json, logging

from ..db import upsert_recipe, get_recipe
from ..llm import claude, _json
from .recipes import _embed

logger = logging.getLogger(__name__)
router = APIRouter()

# ── EPUB parsing ──────────────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    """Strip HTML tags and return clean text."""
    html = re.sub(r'<(script|style)[^>]*>[\s\S]*?</\1>', '', html, flags=re.I)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    html = re.sub(r'</(p|div|h[1-6]|li|tr)>', '\n', html, flags=re.I)
    html = re.sub(r'<[^>]+>', '', html)
    html = re.sub(r'&nbsp;', ' ', html)
    html = re.sub(r'&amp;', '&', html)
    html = re.sub(r'&lt;', '<', html)
    html = re.sub(r'&gt;', '>', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()

def recipe_score(text: str) -> int:
    t = text.lower()
    score = 0
    if re.search(r'\b(ingredient|serves|servings|yield|makes)\b', t): score += 3
    if re.search(r'\b(tablespoon|teaspoon|tbsp|tsp|cup|ounce|pound|gram|oz\b|lb\b)\b', t): score += 3
    if re.search(r'\b(simmer|stir|cook|heat|add|chop|fry|bake|roast|velvet|marinate|brine|whisk)\b', t): score += 2
    if re.search(r'\d+\s*(?:cup|tbsp|tsp|oz|lb|g\b|ml|min|hour)', t): score += 3
    return score

def parse_epub(epub_bytes: bytes, chunk_size: int = 6000, min_score: int = 2) -> list[dict]:
    """Extract recipe-candidate text chunks from EPUB bytes."""
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
            # Split at heading boundaries
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

# ── Claude prompts ────────────────────────────────────────────────────────────

EXTRACT_SYS = """You are a recipe extraction assistant for a cookbook. Extract ALL recipes, techniques, sauces and marinades from the text.

Output ONLY a JSON array. Each object must have:
- name: string
- type: "recipe" | "technique" | "sauce" | "marinade"
- description: string (one sentence)
- yield: string (e.g. "Serves 4")
- totalTime: ISO 8601 string (e.g. "PT30M") or null
- prepTime: ISO 8601 or null
- ingredientGroups: [{name: null, ingredients: [{raw: "2 tablespoons soy sauce", quantity: 2.0, unit: "tablespoon", food: "soy sauce", note: null, display: "2 tablespoons soy sauce"}]}]
- instructions: [{step: 1, text: "...", timer: null}]
- tags: array of strings

Rules:
- Format each ingredient as "{quantity} {unit} {food}" e.g. "2 tablespoons soy sauce". Extract quantities from instruction text if no separate list.
- Include techniques (velveting, brining, stocks) — valuable even without yield.
- Return [] ONLY for clearly non-recipe text (index, bibliography).
- Return ONLY the JSON array, no markdown."""

REPAIR_SYS = """You are repairing an incomplete recipe extraction from a cookbook. Extract the COMPLETE recipe:
- ingredientGroups with properly parsed ingredients: [{raw, quantity, unit, food, note, display}]
- instructions as [{step, text, timer}] — ALL steps
- Also set: type, tags, totalTime, prepTime, yield, description

Return ONLY a JSON array. No markdown."""

def is_partial(r: dict) -> bool:
    ings = sum(len(g.get('ingredients', [])) for g in r.get('ingredientGroups', []))
    steps = len(r.get('instructions', []))
    return ings < 2 or steps < 1

def make_slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9\s-]', '', name.lower())
    s = re.sub(r'[\s-]+', '-', s).strip('-')
    return s[:80]

def normalize_recipe(r: dict, source: str) -> dict:
    """Normalize extracted recipe to Recipe Book schema."""
    slug = make_slug(r.get('name', 'recipe'))
    return {
        'slug': slug,
        'name': r.get('name', ''),
        'description': r.get('description', ''),
        'source': source,
        'subtitle': r.get('type', 'recipe'),
        'yield': r.get('yield') or r.get('recipeYield', ''),
        'totalTime': r.get('totalTime'),
        'prepTime': r.get('prepTime'),
        'tags': r.get('tags', []) + [r.get('type', 'recipe')],
        'ingredientGroups': r.get('ingredientGroups', []),
        'instructions': r.get('instructions', []),
        'note': f"Extracted from EPUB. Type: {r.get('type', 'recipe')}.",
    }

# ── SSE streaming endpoint ────────────────────────────────────────────────────

@router.post("/epub")
async def import_epub(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_size: int = Form(6000),
    min_score: int = Form(2),
    max_chunks: int = Form(500),
    do_repair: bool = Form(True),
    source: str = Form("EPUB Import"),
):
    """
    Upload an EPUB file. Extracts recipes server-side via Claude Haiku,
    optionally repairs partials, imports directly to DB.
    Returns a StreamingResponse with SSE progress events.
    """
    epub_bytes = await file.read()

    async def stream():
        def event(msg: str, data: dict = None):
            payload = json.dumps({'msg': msg, **(data or {})})
            return f"data: {payload}\n\n"

        try:
            yield event(f"Parsing EPUB: {file.filename}...")
            chunks = parse_epub(epub_bytes, chunk_size=chunk_size, min_score=min_score)
            total = min(len(chunks), max_chunks)
            yield event(f"Found {len(chunks)} candidate chunks, processing {total}")

            extracted = []
            dedup = set()

            for i, chunk in enumerate(chunks[:max_chunks]):
                yield event(f"[{i+1}/{total}] {chunk['title'][:50]}...")
                try:
                    raw = await claude(EXTRACT_SYS, f"Extract recipes from this cookbook text:\n\n{chunk['text']}", max_tokens=2000)
                    recipes = _json(raw)
                    if not isinstance(recipes, list):
                        recipes = []
                    for r in recipes:
                        if not r.get('name'):
                            continue
                        slug = make_slug(r['name'])
                        if slug in dedup:
                            continue
                        dedup.add(slug)
                        extracted.append((r, chunk['text']))
                    if recipes:
                        yield event(f"  ✓ {len(recipes)}: {', '.join(r.get('name','?') for r in recipes)[:80]}")
                except Exception as e:
                    yield event(f"  ✗ {str(e)[:60]}")
                await asyncio.sleep(0.3)  # gentle rate limiting

            yield event(f"Extraction complete: {len(extracted)} recipes found")

            # Repair pass
            partials = [(r, text) for r, text in extracted if is_partial(r)]
            if do_repair and partials:
                yield event(f"Repairing {len(partials)} partial recipes...")
                for i, (r, text) in enumerate(partials):
                    yield event(f"[{i+1}/{len(partials)}] Repairing: {r.get('name','?')[:50]}")
                    try:
                        raw = await claude(REPAIR_SYS, f"Extract the complete recipe from this text:\n\n{text}", max_tokens=2000)
                        repaired = _json(raw)
                        if isinstance(repaired, list) and repaired:
                            merged = {**r, **repaired[0]}
                            # Replace in extracted
                            for j, (orig, orig_text) in enumerate(extracted):
                                if orig.get('name') == r.get('name'):
                                    extracted[j] = (merged, orig_text)
                                    break
                            yield event(f"  ✓ Repaired")
                    except Exception as e:
                        yield event(f"  ✗ {str(e)[:60]}")
                    await asyncio.sleep(0.3)

            # Import to DB
            imported, skipped = 0, 0
            for r, _ in extracted:
                normalized = normalize_recipe(r, source)
                # Skip if exists and has content
                existing = get_recipe(normalized['slug'])
                if existing:
                    skipped += 1
                    continue
                upsert_recipe(normalized)
                bg.add_task(_embed, normalized)
                imported += 1

            complete_msg = f"Done! Imported: {imported} | Skipped (duplicates): {skipped} | Total extracted: {len(extracted)}"
            yield event(complete_msg, {'done': True, 'imported': imported, 'skipped': skipped, 'total': len(extracted)})

        except Exception as e:
            yield event(f"Fatal error: {str(e)}", {'error': True})

    return StreamingResponse(stream(), media_type="text/event-stream")
