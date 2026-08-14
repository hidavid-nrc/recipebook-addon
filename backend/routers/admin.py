# backend/routers/admin.py — one-time maintenance jobs.
from fastapi import APIRouter, HTTPException
from ..db import get_conn, backup_db, DB_PATH
from ..tag_rules import normalize_tags
import json

router = APIRouter()

@router.post("/normalize-tags")
async def normalize_all_tags(dry_run: bool = False):
    """One-time (idempotent) tag cleanup: maps the existing free-form tags to
    the controlled vocabulary in tag_rules.py. Takes a backup first unless
    dry_run=true. Safe to re-run — already-clean tags pass through unchanged."""
    backup_path = None
    if not dry_run:
        try:
            backup_path = backup_db()
        except Exception as e:
            raise HTTPException(500, f"Backup failed, aborting migration: {e}")

    with get_conn() as c:
        rows = c.execute("SELECT slug, tags, data FROM recipes").fetchall()

    before_tag_count = set()
    after_tag_count = set()
    changed = 0
    updates = []

    for row in rows:
        old_tags = json.loads(row["tags"])
        before_tag_count.update(t.lower() for t in old_tags)
        new_tags = normalize_tags(old_tags)
        after_tag_count.update(new_tags)
        if sorted(t.lower() for t in old_tags) != new_tags:
            changed += 1
            if not dry_run:
                data = json.loads(row["data"])
                data["tags"] = new_tags
                updates.append((row["slug"], json.dumps(new_tags), json.dumps(data)))

    if not dry_run and updates:
        with get_conn() as c:
            for slug, tags_json, data_json in updates:
                c.execute(
                    "UPDATE recipes SET tags=?, data=?, updated_at=datetime('now') WHERE slug=?",
                    (tags_json, data_json, slug)
                )

    return {
        "dry_run": dry_run,
        "backup": backup_path,
        "recipes_total": len(rows),
        "recipes_changed": changed,
        "distinct_tags_before": len(before_tag_count),
        "distinct_tags_after": len(after_tag_count),
    }

@router.get("/tag-report")
async def tag_report():
    """Read-only: current tag distribution, for verification before/after migration."""
    with get_conn() as c:
        rows = c.execute("SELECT tags FROM recipes").fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for t in json.loads(row["tags"]):
            counts[t] = counts.get(t, 0) + 1
    return {
        "distinct_tags": len(counts),
        "tags": sorted(counts.items(), key=lambda x: -x[1]),
    }

@router.get("/quality-report")
async def quality_report():
    """Read-only: find genuinely broken recipes vs. legitimate sparse content.
    A `technique`/`sauce`/`marinade` with few ingredients is FINE. A `recipe`
    with no steps or <2 ingredients is broken."""
    with get_conn() as c:
        rows = c.execute("SELECT slug, name, data FROM recipes").fetchall()
    broken, thin_recipes, empty_steps = [], [], []
    for row in rows:
        d = json.loads(row["data"])
        subtitle = d.get("subtitle") or "recipe"
        ings = sum(len(g.get("ingredients", [])) for g in d.get("ingredientGroups", []))
        steps = len(d.get("instructions", []))
        # Only judge type=recipe strictly; techniques/sauces can be sparse.
        if subtitle == "recipe":
            if steps == 0 and ings < 2:
                broken.append({"slug": row["slug"], "name": row["name"], "ings": ings, "steps": steps})
            elif ings < 2:
                thin_recipes.append({"slug": row["slug"], "name": row["name"], "ings": ings, "steps": steps})
            elif steps == 0:
                empty_steps.append({"slug": row["slug"], "name": row["name"], "ings": ings, "steps": steps})
    return {
        "total": len(rows),
        "fully_broken": {"count": len(broken), "items": broken},
        "thin_no_ingredients": {"count": len(thin_recipes), "items": thin_recipes[:50]},
        "no_steps": {"count": len(empty_steps), "items": empty_steps[:50]},
    }

@router.post("/delete-broken")
async def delete_broken(confirm: bool = False):
    """Delete type=recipe entries that are fully broken (0 steps AND <2 ingredients).
    Requires confirm=true. Takes a backup first. Techniques/sauces never touched."""
    if not confirm:
        raise HTTPException(400, "Pass confirm=true to actually delete. Run /quality-report first.")
    backup_path = backup_db()
    with get_conn() as c:
        rows = c.execute("SELECT slug, data FROM recipes").fetchall()
        to_delete = []
        for row in rows:
            d = json.loads(row["data"])
            if (d.get("subtitle") or "recipe") != "recipe":
                continue
            ings = sum(len(g.get("ingredients", [])) for g in d.get("ingredientGroups", []))
            steps = len(d.get("instructions", []))
            if steps == 0 and ings < 2:
                to_delete.append(row["slug"])
        for slug in to_delete:
            c.execute("DELETE FROM recipes WHERE slug=?", (slug,))
    return {"backup": backup_path, "deleted": len(to_delete), "slugs": to_delete}

# ── Recipe repair (missing steps / missing ingredients) ───────────────────────
import asyncio
from ..llm import claude, _json, MODEL_FAST

REPAIR_STEPS_SYS = """You are reconstructing cooking instructions for a recipe that has a
complete ingredient list but lost its method during a prior data-extraction pass.
You do NOT have the original source text — only the recipe name, source, tags, and
ingredient list. Write a plausible, technically sound method consistent with
standard technique for this dish and cuisine.

Return ONLY a JSON array of steps: [{"step": 1, "text": "...", "timer": null}]
Use metric units (g, ml, °C) where relevant. Be concrete about heat level, timing,
and order of operations. Return ONLY the JSON array, no markdown."""

REPAIR_INGREDIENTS_SYS = """You are reconstructing an ingredient list for a recipe that lost
its ingredients during a prior data-extraction pass. You do NOT have the original
source text — only the recipe name, source, tags, and any existing instructions.
Infer a plausible, complete ingredient list consistent with the dish and cuisine.

Return ONLY a JSON array: [{"raw":"200g beef, thinly sliced","quantity":200,"unit":"g","food":"beef","note":"thinly sliced","display":"200g beef, thinly sliced"}]
Use metric units. Return ONLY the JSON array, no markdown."""

RECONSTRUCTED_NOTE = "⚠️ AI-reconstructed: the original source text was lost before this could be re-extracted verbatim. This {field} was inferred by Haiku from the recipe's {basis} — verify against the source cookbook before relying on it."

@router.post("/repair-recipes")
async def repair_recipes(dry_run: bool = False, confirm: bool = False):
    """Repair recipes with missing steps (has ingredients, 0 instructions) or
    missing ingredients (0 ingredients, has instructions/name). Uses Haiku to
    RECONSTRUCT plausible content from what IS present — this is not a re-extraction
    from source (that text no longer exists), so every repaired field is flagged
    in the recipe's note for manual verification. Takes a backup first (real run only).
    """
    if not dry_run and not confirm:
        raise HTTPException(400, "Pass confirm=true for a real run, or dry_run=true to preview.")

    with get_conn() as c:
        rows = c.execute("SELECT slug, name, data FROM recipes").fetchall()

    targets = []
    for row in rows:
        d = json.loads(row["data"])
        if (d.get("subtitle") or "recipe") != "recipe":
            continue
        ings = sum(len(g.get("ingredients", [])) for g in d.get("ingredientGroups", []))
        steps = len(d.get("instructions", []))
        if steps == 0 and ings >= 2:
            targets.append(("steps", row["slug"], d))
        elif ings == 0 and steps >= 1:
            targets.append(("ingredients", row["slug"], d))

    if dry_run:
        return {
            "dry_run": True,
            "would_repair": len(targets),
            "missing_steps": len([t for t in targets if t[0] == "steps"]),
            "missing_ingredients": len([t for t in targets if t[0] == "ingredients"]),
            "slugs": [t[1] for t in targets],
        }

    if not targets:
        return {"repaired": 0, "message": "Nothing to repair."}

    backup_path = backup_db()
    repaired, failed = [], []

    for kind, slug, d in targets:
        try:
            if kind == "steps":
                ing_text = "\n".join(
                    i.get("raw", i.get("display", i.get("food", "")))
                    for g in d.get("ingredientGroups", []) for i in g.get("ingredients", [])
                )
                prompt = f"Recipe: {d.get('name')}\nSource: {d.get('source','')}\nTags: {', '.join(d.get('tags',[]))}\n\nIngredients:\n{ing_text}"
                raw = await claude(REPAIR_STEPS_SYS, prompt, max_tokens=2048, model=MODEL_FAST)
                steps = _json(raw)
                # Validate shape before trusting it — this repairs real content, no silent garbage.
                valid = isinstance(steps, list) and steps and all(
                    isinstance(s, dict) and s.get("text") for s in steps
                )
                if valid:
                    d["instructions"] = steps
                    d["note"] = (d.get("note") or "") + "\n" + RECONSTRUCTED_NOTE.format(field="method", basis="ingredient list")
                    repaired.append(slug)
                else:
                    failed.append({"slug": slug, "error": "Haiku response failed shape validation (steps)"})
            else:  # missing ingredients
                steps_text = "\n".join(f"{s.get('step','')}. {s.get('text','')}" for s in d.get("instructions", []))
                prompt = f"Recipe: {d.get('name')}\nSource: {d.get('source','')}\nTags: {', '.join(d.get('tags',[]))}\n\nExisting steps:\n{steps_text}"
                raw = await claude(REPAIR_INGREDIENTS_SYS, prompt, max_tokens=2048, model=MODEL_FAST)
                ings_list = _json(raw)
                valid = isinstance(ings_list, list) and ings_list and all(
                    isinstance(i, dict) and i.get("food") for i in ings_list
                )
                if valid:
                    d["ingredientGroups"] = [{"name": None, "ingredients": ings_list}]
                    d["note"] = (d.get("note") or "") + "\n" + RECONSTRUCTED_NOTE.format(field="ingredient list", basis="recipe name and existing steps")
                    repaired.append(slug)
                else:
                    failed.append({"slug": slug, "error": "Haiku response failed shape validation (ingredients)"})
        except Exception as e:
            failed.append({"slug": slug, "error": str(e)[:200]})

        await asyncio.sleep(2.0)  # match existing rate-limit discipline

        if slug in repaired:
            with get_conn() as c:
                c.execute(
                    "UPDATE recipes SET data=?, tags=?, updated_at=datetime('now') WHERE slug=?",
                    (json.dumps(d), json.dumps(d.get("tags", [])), slug)
                )

    return {
        "backup": backup_path,
        "attempted": len(targets),
        "repaired": len(repaired),
        "failed": len(failed),
        "failed_details": failed,
        "repaired_slugs": repaired,
    }
