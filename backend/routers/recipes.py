from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from ..db import (upsert_recipe, get_recipe, list_recipes, delete_recipe,
                  save_embedding, get_note, save_note, get_prefs,
                  add_cook, get_cooks, recent_cooks, cook_summary)
from ..llm import embed, recipe_text, semantic_search

router = APIRouter()

class NoteIn(BaseModel):
    note: str

class CookIn(BaseModel):
    rating: Optional[int] = None
    notes: str = ""

# ── Embed helper ──────────────────────────────────────────────
async def _embed(data: dict):
    try:
        vec = await embed(recipe_text(data))
        save_embedding(data["slug"], vec)
    except Exception as e:
        print(f"Embed error {data.get('slug')}: {e}")

# Static routes MUST be declared before /{slug} to avoid shadowing
@router.get("/embed-all")
async def embed_all(bg: BackgroundTasks):
    slugs = [r["slug"] for r in list_recipes() if not r.get("embedding")]
    for r in list_recipes():
        if not r.get("embedding"):
            bg.add_task(_embed, r["data"])
    return {"queued": len(slugs)}

@router.get("/cooked-recent")
async def cooked_recent(limit: int = Query(50, le=200)):
    return recent_cooks(limit)

@router.get("")
async def list_all(
    search: str = Query(""),
    tags: list[str] = Query([]),
    rtype: str = Query("", description="filter by subtitle type: recipe|technique|sauce|marinade"),
    semantic: bool = Query(False)
):
    result = list_recipes(search=search, tags=tags, rtype=rtype)
    if semantic and search:
        prefs = get_prefs().get("structured_json", {})
        result = await semantic_search(search, result, prefs)
    return result

@router.post("", status_code=201)
async def create(recipe: dict, bg: BackgroundTasks):
    if not recipe.get("slug") or not recipe.get("name"):
        raise HTTPException(400, "recipe needs slug and name")
    saved = upsert_recipe(recipe)
    bg.add_task(_embed, recipe)
    return saved

@router.post("/batch", status_code=201)
async def batch(recipes: list[dict], bg: BackgroundTasks):
    saved = []
    for r in recipes:
        if not r.get("slug") or not r.get("name"):
            continue
        upsert_recipe(r)
        bg.add_task(_embed, r)
        saved.append(r["name"])
    return {"imported": len(saved), "names": saved}

@router.get("/{slug}")
async def get_one(slug: str):
    r = get_recipe(slug)
    if not r:
        raise HTTPException(404, f"Recipe '{slug}' not found")
    r["note"] = get_note(slug)
    r["cooks"] = cook_summary(slug)
    return r

@router.delete("/{slug}", status_code=204)
async def remove(slug: str):
    if not get_recipe(slug):
        raise HTTPException(404)
    delete_recipe(slug)

@router.get("/{slug}/note")
async def note_get(slug: str):
    return {"note": get_note(slug)}

@router.put("/{slug}/note")
async def note_put(slug: str, body: NoteIn):
    if not get_recipe(slug):
        raise HTTPException(404)
    save_note(slug, body.note)
    return {"note": body.note}

# ── Cook log / ratings ───────────────────────────────────────
@router.post("/{slug}/cook", status_code=201)
async def log_cook(slug: str, body: CookIn):
    if not get_recipe(slug):
        raise HTTPException(404, f"Recipe '{slug}' not found")
    if body.rating is not None and not (1 <= body.rating <= 5):
        raise HTTPException(400, "rating must be 1-5")
    logged = add_cook(slug, body.rating, body.notes)
    return {"logged": logged, "summary": cook_summary(slug)}

@router.get("/{slug}/cook")
async def list_cooks(slug: str):
    if not get_recipe(slug):
        raise HTTPException(404)
    return get_cooks(slug)
