from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from ..db import upsert_recipe
from ..llm import scrape_url, parse_html
from .recipes import _embed

router = APIRouter()

class URLIn(BaseModel):
    url: str

class HTMLIn(BaseModel):
    html: str
    source: Optional[str] = None

class PushIn(BaseModel):
    recipes: list[dict]

async def _save_many(recipes: list, bg: BackgroundTasks, source: str = None) -> dict:
    saved = []
    for r in recipes:
        if not r.get("slug") or not r.get("name"):
            continue
        if source and not r.get("source"):
            r["source"] = source
        upsert_recipe(r)
        bg.add_task(_embed, r)
        saved.append(r.get("name", "?"))
    return {"imported": len(saved), "names": saved}

@router.post("/url")
async def from_url(body: URLIn, bg: BackgroundTasks):
    try:
        recipes = await scrape_url(body.url)
    except Exception as e:
        raise HTTPException(422, f"Scrape failed: {e}")
    if not recipes:
        raise HTTPException(422, "No recipes found at this URL")
    return await _save_many(recipes, bg, body.url)

@router.post("/html")
async def from_html(body: HTMLIn, bg: BackgroundTasks):
    try:
        recipes = await parse_html(body.html)
    except Exception as e:
        raise HTTPException(422, f"Parse failed: {e}")
    if not recipes:
        raise HTTPException(422, "No recipes found in HTML")
    return await _save_many(recipes, bg, body.source)

@router.post("/push")
async def push(body: PushIn, bg: BackgroundTasks):
    return await _save_many(body.recipes, bg)
