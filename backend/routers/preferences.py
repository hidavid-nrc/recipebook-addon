from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import get_prefs, save_prefs, list_recipes
from ..llm import extract_prefs, gap_analysis

router = APIRouter()

class PrefsIn(BaseModel):
    raw_text: str

@router.get("")
async def get():
    return get_prefs()

@router.post("")
async def save(body: PrefsIn):
    try:
        structured = await extract_prefs(body.raw_text)
    except Exception as e:
        raise HTTPException(422, str(e))
    save_prefs(body.raw_text, structured)
    return get_prefs()

@router.post("/gaps")
async def gaps():
    prefs = get_prefs()
    if not prefs.get("raw_text"): raise HTTPException(400, "No preferences set")
    recipes = list_recipes()
    if not recipes: raise HTTPException(400, "No recipes yet")
    analysis = await gap_analysis(recipes, prefs.get("structured_json", {}))
    return {"analysis": analysis}
