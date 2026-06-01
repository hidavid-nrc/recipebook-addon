from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
import re, os

from ..db import get_week, set_slot, clear_slot, list_recipes, get_recipe, get_prefs
from ..llm import gen_meal_plan, push_to_ha_todo

router = APIRouter()

def this_monday() -> str:
    t = date.today()
    return str(t - timedelta(days=t.weekday()))

def _base_servings(data: dict) -> float:
    """Parse base servings from yield string. '8 to 12' → 10.0, '4 servings' → 4.0"""
    yield_str = data.get("yield", "") or ""
    nums = [int(n) for n in re.findall(r"\d+", yield_str)]
    if not nums:
        return 2.0
    return sum(nums) / len(nums)

def _build_items(week_start: str) -> tuple[list[str], list[dict]]:
    slots = get_week(week_start)
    bring, display = [], []
    seen: set[str] = set()
    for slot in slots:
        slug = slot.get("recipe_slug")
        if not slug:
            continue
        r = get_recipe(slug)
        if not r:
            continue
        base = _base_servings(r["data"])
        ratio = (slot.get("servings") or 2) / base if base else 1.0
        for g in r["data"].get("ingredientGroups", []):
            for ing in g.get("ingredients", []):
                food = (ing.get("food") or "").strip()
                if not food or food in seen:
                    continue
                seen.add(food)
                qty = ing.get("quantity")
                unit = ing.get("unit") or ""
                scaled = round(qty * ratio, 2) if qty else None
                disp = f"{scaled} {unit} {food}".strip() if scaled else food
                bring.append(disp)
                display.append({"food": food, "display": disp, "recipe": slot.get("recipe_name", "")})
    return bring, display

class SlotIn(BaseModel):
    recipe_slug: Optional[str] = None
    servings: float = 2

class GenerateIn(BaseModel):
    week_start: Optional[str] = None

class BringIn(BaseModel):
    week_start: Optional[str] = None

@router.get("")
async def get_plan(week_start: str = Query(default="")):
    ws = week_start or this_monday()
    return {"week_start": ws, "slots": get_week(ws)}

@router.put("/{week_start}/{day}/{slot}")
async def set_plan_slot(week_start: str, day: int, slot: str, body: SlotIn):
    if slot not in ("lunch", "dinner"):
        raise HTTPException(400, "slot must be lunch or dinner")
    if not 0 <= day <= 6:
        raise HTTPException(400, "day must be 0-6")
    if body.recipe_slug is None:
        clear_slot(week_start, day, slot)
    else:
        set_slot(week_start, day, slot, body.recipe_slug, body.servings)
    return {"week_start": week_start, "slots": get_week(week_start)}

@router.delete("/{week_start}/{day}/{slot}", status_code=204)
async def del_slot(week_start: str, day: int, slot: str):
    clear_slot(week_start, day, slot)

@router.post("/generate")
async def generate(body: GenerateIn):
    ws = body.week_start or this_monday()
    recipes = list_recipes()
    if not recipes:
        raise HTTPException(400, "No recipes in collection")
    prefs = get_prefs().get("structured_json", {})
    recent = []
    for i in range(1, 3):
        past = str(date.fromisoformat(ws) - timedelta(weeks=i))
        recent += [s["recipe_slug"] for s in get_week(past) if s.get("recipe_slug")]
    try:
        slots = await gen_meal_plan(recipes, prefs, recent, ws)
    except Exception as e:
        raise HTTPException(422, str(e))
    for s in slots:
        set_slot(ws, s["day"], s["slot"], s.get("recipe_slug"), s.get("servings", 2))
    return {"week_start": ws, "slots": get_week(ws)}

@router.post("/bring")
async def send_bring(body: BringIn):
    ws = body.week_start or this_monday()
    entity = os.environ.get("BRING_ENTITY", "todo.spezialassu")
    bring_items, _ = _build_items(ws)
    if not bring_items:
        raise HTTPException(400, "No ingredients in this week's plan")
    try:
        await push_to_ha_todo(bring_items, entity)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"pushed": len(bring_items), "entity": entity}

@router.get("/{week_start}/shopping-list")
async def shopping_list(week_start: str):
    _, display = _build_items(week_start)
    return {"week_start": week_start, "items": display}
