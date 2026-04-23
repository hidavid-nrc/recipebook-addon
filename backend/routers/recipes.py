from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
import asyncio

from ..db import (upsert_recipe, get_recipe, list_recipes, delete_recipe,
                  save_embedding, get_note, save_note, get_prefs)
from ..llm import embed, recipe_text, semantic_search

router = APIRouter()

class NoteIn(BaseModel):
    note: str

# ── Embed helper ──────────────────────────────────────────────
async def _embed(data: dict):
    try:
        vec = await embed(recipe_text(data))
        save_embedding(data["slug"], vec)
    except Exception as e:
        print(f"Embed error {data.get('slug')}: {e}")

# ── CRUD ──────────────────────────────────────────────────────

# Static routes MUST be declared before /{slug} to avoid shadowing
@router.get("/embed-all")
async def embed_all(bg: BackgroundTasks):
    """Queue embedding for all un-indexed recipes."""
    slugs = [r["slug"] for r in list_recipes() if not r.get("embedding")]
    for r in list_recipes():
        if not r.get("embedding"):
            bg.add_task(_embed, r["data"])
    return {"queued": len(slugs)}

@router.get("")
async def list_all(
    search: str = Query(""),
    tags: list[str] = Query([]),
    semantic: bool = Query(False)
):
    result = list_recipes(search=search, tags=tags)
    if semantic and search:
        prefs = get_prefs().get("structured_json", {})
        result = await semantic_search(search, result, prefs)
    return result

@router.post("", status_code=201)
async def create(recipe: dict, bg: BackgroundTasks):
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
