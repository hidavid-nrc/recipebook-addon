# backend/routers/pdf_import.py — PDF cookbook import via pdfminer.six + Haiku
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException
import asyncio, io, re, json, logging, uuid
from datetime import datetime

from ..db import upsert_recipe, get_recipe
from ..llm import MODEL_FAST
from .epub import haiku, _json, normalize_recipe, is_partial, make_slug, log_job, _jobs, EXTRACT_SYS, REPAIR_SYS
from .recipes import _embed

logger = logging.getLogger(__name__)
router = APIRouter()

# ── PDF text extraction ──────────────────────────────────────────────────────

def extract_pdf_pages(pdf_bytes: bytes) -> list[dict]:
    """Extract text page-by-page using pdfminer.six. Returns list of {page, text}."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LAParams

    pages = []
    laparams = LAParams(line_margin=0.5, word_margin=0.1, char_margin=2.0)

    try:
        for i, page_layout in enumerate(extract_pages(io.BytesIO(pdf_bytes), laparams=laparams)):
            text_parts = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    text_parts.append(element.get_text())
            text = "\n".join(text_parts).strip()
            if text:
                pages.append({"page": i + 1, "text": text})
    except Exception as e:
        raise ValueError(f"PDF parsing failed: {e}")

    return pages

def recipe_score(text: str) -> int:
    """Same scoring as epub.py — detect recipe-like content."""
    t = text.lower()
    score = 0
    if re.search(r'\b(ingredient|serves|servings|yield|makes|pour)\b', t): score += 3
    if re.search(r'\b(tablespoon|teaspoon|tbsp|tsp|cup|ounce|pound|gram|oz\b|lb\b|ml\b|cl\b)\b', t): score += 3
    if re.search(r'\b(simmer|stir|cook|heat|add|chop|fry|bake|roast|blend|steam|knead|whisk|sauté)\b', t): score += 2
    if re.search(r'\d+\s*(?:cup|tbsp|tsp|oz|lb|g\b|ml|min|hour|minute)', t): score += 3
    # Appliance-specific signals (Cook Expert, Thermomix, etc.)
    if re.search(r'\b(speed\s*\d|programme?|auto.?stir|simmer.*stir|expert\s*mode)\b', t, re.I): score += 2
    return score

def chunk_pages(pages: list[dict], min_score: int = 2) -> list[dict]:
    """Group pages into recipe-likely chunks. Consecutive pages with score >= min_score
    are merged (recipes often span 2 pages in cookbooks)."""
    chunks = []
    current_text = ""
    current_start = None

    for p in pages:
        score = recipe_score(p["text"])
        if score >= min_score:
            if current_start is None:
                current_start = p["page"]
            current_text += "\n\n" + p["text"]
            # Cap at ~8000 chars per chunk (approx 2-3 pages)
            if len(current_text) > 8000:
                title_match = re.search(r'^([A-Z][A-Za-z\s\-\'&,]{3,60})$', current_text.strip(), re.M)
                title = title_match.group(1).strip() if title_match else f"Page {current_start}"
                chunks.append({"title": title, "text": current_text.strip(), "pages": f"{current_start}-{p['page']}"})
                current_text = ""
                current_start = None
        else:
            # Non-recipe page breaks the chunk
            if current_text and current_start is not None:
                title_match = re.search(r'^([A-Z][A-Za-z\s\-\'&,]{3,60})$', current_text.strip(), re.M)
                title = title_match.group(1).strip() if title_match else f"Page {current_start}"
                chunks.append({"title": title, "text": current_text.strip(), "pages": f"{current_start}-{p['page']-1}"})
            current_text = ""
            current_start = None

    # Flush last chunk
    if current_text and current_start is not None:
        title_match = re.search(r'^([A-Z][A-Za-z\s\-\'&,]{3,60})$', current_text.strip(), re.M)
        title = title_match.group(1).strip() if title_match else f"Page {current_start}"
        chunks.append({"title": title, "text": current_text.strip(), "pages": f"{current_start}-end"})

    return chunks

# ── Background extraction task ────────────────────────────────────────────────

async def _run_pdf_import(job_id: str, pdf_bytes: bytes, min_score: int,
                           max_chunks: int, do_repair: bool, source: str,
                           appliance_mode: bool, bg: BackgroundTasks):
    job = _jobs[job_id]
    job['status'] = 'running'

    try:
        log_job(job_id, "Extracting text from PDF...")
        pages = extract_pdf_pages(pdf_bytes)
        total_pages = len(pages)

        if total_pages == 0:
            log_job(job_id, "No text found in PDF — it may be a scanned/image PDF. Try OCR first.", 'error')
            job['status'] = 'error'
            return

        # Check for scanned PDF (very little text per page)
        avg_chars = sum(len(p["text"]) for p in pages) / total_pages
        if avg_chars < 50:
            log_job(job_id, f"Very little text found (avg {avg_chars:.0f} chars/page) — likely a scanned PDF. Use OCR first.", 'error')
            job['status'] = 'error'
            return

        log_job(job_id, f"Extracted {total_pages} pages (avg {avg_chars:.0f} chars/page)")

        log_job(job_id, "Chunking pages by recipe signals...")
        chunks = chunk_pages(pages, min_score=min_score)[:max_chunks]
        total = len(chunks)
        log_job(job_id, f"Found {total} recipe-likely chunks")
        job['stats']['total_chunks'] = total

        # Enhanced prompt for appliance cookbooks
        extract_sys = EXTRACT_SYS
        if appliance_mode:
            extract_sys += """

IMPORTANT — APPLIANCE COOKBOOK:
Preserve ALL appliance-specific instructions exactly as written:
- Program names (Expert, P1, P2, Simmer & Stir, Knead, etc.)
- Speed settings (speed 2A, speed 15, etc.)
- Temperature settings from the device
- Timer durations set on the device
These are NOT generic cooking instructions — they control a specific machine.
Tag each recipe with 'appliance' and the device name if identifiable."""

        extracted = []
        dedup = set()
        failed_chunks = []

        for i, chunk in enumerate(chunks):
            log_job(job_id, f"[{i+1}/{total}] (pages {chunk['pages']}) {chunk['title'][:50]}...")
            job['stats']['chunk'] = i + 1
            try:
                raw = await haiku(extract_sys, f"Extract recipes from this cookbook text:\n\n{chunk['text']}")
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
                    log_job(job_id, f"  ✓ {len(new_recipes)}: {', '.join(new_recipes)[:100]}", 'ok')
            except Exception as e:
                log_job(job_id, f"  ✗ {str(e)[:300]}", 'error')
                failed_chunks.append(i)
            await asyncio.sleep(2.0)

        log_job(job_id, f"Extraction done: {len(extracted)} recipes from {total} chunks ({len(failed_chunks)} failed)")
        job['stats']['extracted'] = len(extracted)
        job['stats']['failed_chunks'] = len(failed_chunks)

        # Repair pass
        if do_repair:
            partials = [(r, text) for r, text in extracted if is_partial(r)]
            if partials:
                log_job(job_id, f"Repairing {len(partials)} partial recipes...")
                for i, (r, text) in enumerate(partials):
                    log_job(job_id, f"[{i+1}/{len(partials)}] Repairing: {r.get('name','?')[:50]}")
                    try:
                        original_type = r.get('type', 'recipe')
                        raw = await haiku(REPAIR_SYS, f"Original type: {original_type}\n\nCookbook text:\n\n{text}")
                        repaired = _json(raw)
                        if isinstance(repaired, list) and repaired:
                            fixed = repaired[0]
                            if not fixed.get('type'):
                                fixed['type'] = original_type
                            merged = {**r, **fixed}
                            for j, (orig, orig_text) in enumerate(extracted):
                                if orig.get('name') == r.get('name'):
                                    extracted[j] = (merged, orig_text)
                                    break
                            log_job(job_id, f"  ✓ repaired", 'ok')
                    except Exception as e:
                        log_job(job_id, f"  ✗ {str(e)[:300]}", 'error')
                    await asyncio.sleep(2.0)

        # Import to DB
        imported, skipped = 0, 0
        for r, _ in extracted:
            normalized = normalize_recipe(r, source)
            if appliance_mode and 'appliance' not in normalized.get('tags', []):
                normalized['tags'] = normalized.get('tags', []) + ['appliance']
            existing = get_recipe(normalized['slug'])
            if existing:
                skipped += 1
                continue
            upsert_recipe(normalized)
            bg.add_task(_embed, normalized)
            imported += 1

        job['stats']['imported'] = imported
        job['stats']['skipped'] = skipped
        log_job(job_id, f"Done! Imported: {imported} | Skipped (dupes): {skipped} | Failed chunks: {len(failed_chunks)}", 'done')
        job['status'] = 'done'

    except ValueError as e:
        log_job(job_id, str(e), 'error')
        job['status'] = 'error'
    except Exception as e:
        log_job(job_id, f"Fatal error: {str(e)}", 'error')
        job['status'] = 'error'

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/pdf")
async def import_pdf(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    min_score: int = Form(2),
    max_chunks: int = Form(300),
    do_repair: str = Form('true'),
    source: str = Form(""),
    appliance_mode: str = Form('false'),
):
    """Start a PDF import job. Returns job_id. Poll /api/ingest/pdf/{job_id} for progress."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Please upload a PDF file")

    pdf_bytes = await file.read()
    job_id = str(uuid.uuid4())[:8]
    final_source = source or file.filename.replace('.pdf', '').replace('.PDF', '')
    repair = do_repair.lower() in ('true', '1', 'yes')
    appliance = appliance_mode.lower() in ('true', '1', 'yes')

    _jobs[job_id] = {
        'status': 'queued',
        'filename': file.filename,
        'log': [],
        'stats': {'chunk': 0, 'total_chunks': 0, 'extracted': 0, 'imported': 0, 'skipped': 0, 'failed_chunks': 0},
        'created': datetime.utcnow().isoformat(),
    }

    bg.add_task(_run_pdf_import, job_id, pdf_bytes, min_score, max_chunks,
                repair, final_source, appliance, bg)
    return {'job_id': job_id, 'status': 'queued'}

@router.get("/pdf/{job_id}")
async def pdf_job_status(job_id: str, since: int = 0):
    """Poll import job status."""
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
