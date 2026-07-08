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
