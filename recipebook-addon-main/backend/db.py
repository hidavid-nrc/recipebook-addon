import sqlite3, json, os
from pathlib import Path

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH  = os.path.join(DATA_DIR, "recipes.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS recipes (
                slug        TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                source      TEXT,
                tags        TEXT DEFAULT '[]',
                data        TEXT NOT NULL,
                embedding   TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS notes (
                recipe_slug TEXT PRIMARY KEY REFERENCES recipes(slug) ON DELETE CASCADE,
                note        TEXT NOT NULL DEFAULT '',
                updated_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS meal_plan (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start  TEXT NOT NULL,
                day         INTEGER NOT NULL CHECK(day BETWEEN 0 AND 6),
                slot        TEXT NOT NULL CHECK(slot IN ('lunch','dinner')),
                recipe_slug TEXT REFERENCES recipes(slug) ON DELETE SET NULL,
                servings    REAL DEFAULT 2,
                UNIQUE(week_start, day, slot)
            );
            CREATE TABLE IF NOT EXISTS preferences (
                id              INTEGER PRIMARY KEY CHECK(id=1),
                raw_text        TEXT DEFAULT '',
                structured_json TEXT DEFAULT '{}',
                updated_at      TEXT DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO preferences(id) VALUES(1);
        """)

# ── Recipes ──────────────────────────────────────────────────
def upsert_recipe(data: dict) -> dict:
    slug = data["slug"]
    tags = json.dumps(data.get("tags", []))
    blob = json.dumps(data)
    with get_conn() as c:
        c.execute("""
            INSERT INTO recipes(slug,name,source,tags,data)
            VALUES(?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name, source=excluded.source,
                tags=excluded.tags, data=excluded.data,
                updated_at=datetime('now')
        """, (slug, data.get("name",""), data.get("source",""), tags, blob))
    return get_recipe(slug)

def get_recipe(slug: str) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM recipes WHERE slug=?", (slug,)).fetchone()
        return _r(row) if row else None

def list_recipes(search: str = "", tags: list = []) -> list:
    with get_conn() as c:
        q = "SELECT * FROM recipes"
        p = []
        if search:
            q += " WHERE (name LIKE ? OR source LIKE ?)"
            p = [f"%{search}%", f"%{search}%"]
        q += " ORDER BY name ASC"
        rows = c.execute(q, p).fetchall()
    result = [_r(r) for r in rows]
    if tags:
        result = [r for r in result if any(t in r["tags"] for t in tags)]
    return result

def delete_recipe(slug: str):
    with get_conn() as c:
        c.execute("DELETE FROM recipes WHERE slug=?", (slug,))

def save_embedding(slug: str, vec: list):
    with get_conn() as c:
        c.execute("UPDATE recipes SET embedding=? WHERE slug=?", (json.dumps(vec), slug))

def _r(row) -> dict:
    d = dict(row)
    d["data"]      = json.loads(d["data"])
    d["tags"]      = json.loads(d["tags"])
    d["embedding"] = json.loads(d["embedding"]) if d["embedding"] else None
    return d

# ── Notes ────────────────────────────────────────────────────
def get_note(slug: str) -> str:
    with get_conn() as c:
        row = c.execute("SELECT note FROM notes WHERE recipe_slug=?", (slug,)).fetchone()
        return row["note"] if row else ""

def save_note(slug: str, note: str):
    with get_conn() as c:
        c.execute("""
            INSERT INTO notes(recipe_slug, note) VALUES(?,?)
            ON CONFLICT(recipe_slug) DO UPDATE SET note=excluded.note, updated_at=datetime('now')
        """, (slug, note))

# ── Meal Plan ────────────────────────────────────────────────
def get_week(week_start: str) -> list:
    with get_conn() as c:
        rows = c.execute("""
            SELECT mp.*, r.name as recipe_name, r.tags as recipe_tags
            FROM meal_plan mp
            LEFT JOIN recipes r ON mp.recipe_slug=r.slug
            WHERE mp.week_start=?
            ORDER BY mp.day, mp.slot
        """, (week_start,)).fetchall()
        return [dict(r) for r in rows]

def set_slot(week_start: str, day: int, slot: str, slug: str | None, servings: float = 2):
    with get_conn() as c:
        c.execute("""
            INSERT INTO meal_plan(week_start,day,slot,recipe_slug,servings)
            VALUES(?,?,?,?,?)
            ON CONFLICT(week_start,day,slot) DO UPDATE SET
                recipe_slug=excluded.recipe_slug, servings=excluded.servings
        """, (week_start, day, slot, slug, servings))

def clear_slot(week_start: str, day: int, slot: str):
    with get_conn() as c:
        c.execute("DELETE FROM meal_plan WHERE week_start=? AND day=? AND slot=?",
                  (week_start, day, slot))

# ── Preferences ──────────────────────────────────────────────
def get_prefs() -> dict:
    with get_conn() as c:
        row = c.execute("SELECT * FROM preferences WHERE id=1").fetchone()
        d = dict(row)
        d["structured_json"] = json.loads(d["structured_json"])
        return d

def save_prefs(raw: str, structured: dict):
    with get_conn() as c:
        c.execute("""
            UPDATE preferences SET raw_text=?, structured_json=?, updated_at=datetime('now')
            WHERE id=1
        """, (raw, json.dumps(structured)))
