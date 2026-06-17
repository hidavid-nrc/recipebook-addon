import sqlite3, json, os, glob
from pathlib import Path
from datetime import datetime

DATA_DIR   = os.environ.get("DATA_DIR", "/share/recipebook")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backup/recipebook")
DB_PATH    = os.path.join(DATA_DIR, "recipes.db")

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
            CREATE TABLE IF NOT EXISTS cook_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_slug TEXT REFERENCES recipes(slug) ON DELETE CASCADE,
                cooked_at   TEXT DEFAULT (datetime('now')),
                rating      INTEGER CHECK(rating BETWEEN 1 AND 5),
                notes       TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cook_log_slug ON cook_log(recipe_slug);
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

def list_recipes(search: str = "", tags: list = [], rtype: str = "") -> list:
    """Text search now also matches the JSON blob (ingredients, instructions,
    notes) — not just name/source — closing the old 'weak search' gap."""
    with get_conn() as c:
        q = "SELECT * FROM recipes"
        p = []
        if search:
            q += " WHERE (name LIKE ? OR source LIKE ? OR data LIKE ?)"
            like = f"%{search}%"
            p = [like, like, like]
        q += " ORDER BY name ASC"
        rows = c.execute(q, p).fetchall()
    result = [_r(r) for r in rows]
    if tags:
        result = [r for r in result if any(t in r["tags"] for t in tags)]
    if rtype:
        result = [r for r in result if (r["data"].get("subtitle") or "") == rtype]
    return result

def delete_recipe(slug: str):
    with get_conn() as c:
        c.execute("DELETE FROM recipes WHERE slug=?", (slug,))

def save_embedding(slug: str, vec: list):
    with get_conn() as c:
        c.execute("UPDATE recipes SET embedding=? WHERE slug=?", (json.dumps(vec), slug))

def all_recipes_export() -> list:
    """Full export payload — recipe data + note, embeddings excluded (regenerable)."""
    with get_conn() as c:
        rows = c.execute("SELECT slug,data,created_at,updated_at FROM recipes ORDER BY name").fetchall()
        notes = {r["recipe_slug"]: r["note"] for r in c.execute("SELECT * FROM notes").fetchall()}
    out = []
    for row in rows:
        d = json.loads(row["data"])
        d["_note"] = notes.get(row["slug"], "")
        d["_created_at"] = row["created_at"]
        d["_updated_at"] = row["updated_at"]
        out.append(d)
    return out

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

# ── Cook log / ratings ───────────────────────────────────────
def add_cook(slug: str, rating: int | None = None, notes: str = "") -> dict:
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO cook_log(recipe_slug,rating,notes) VALUES(?,?,?)",
            (slug, rating, notes or ""))
        cid = cur.lastrowid
        row = c.execute("SELECT * FROM cook_log WHERE id=?", (cid,)).fetchone()
        return dict(row)

def get_cooks(slug: str) -> list:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM cook_log WHERE recipe_slug=? ORDER BY cooked_at DESC", (slug,)).fetchall()
        return [dict(r) for r in rows]

def cook_summary(slug: str) -> dict:
    """Shape consumed by the frontend's cookSummaryText()."""
    with get_conn() as c:
        row = c.execute("""
            SELECT COUNT(*) AS count,
                   MAX(cooked_at) AS last_cooked,
                   ROUND(AVG(rating), 1) AS avg_rating
            FROM cook_log WHERE recipe_slug=?
        """, (slug,)).fetchone()
    d = dict(row)
    if not d["count"]:
        return {"count": 0, "last_cooked": None, "avg_rating": None}
    return d

def recent_cooks(limit: int = 50) -> list:
    with get_conn() as c:
        rows = c.execute("""
            SELECT cl.*, r.name AS recipe_name
            FROM cook_log cl LEFT JOIN recipes r ON cl.recipe_slug = r.slug
            ORDER BY cl.cooked_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

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

# ── Backup (safe for a live WAL database) ────────────────────
def backup_db(keep: int = 14) -> str | None:
    """Consistent online backup via VACUUM INTO. Returns the backup path."""
    if not os.path.exists(DB_PATH):
        return None
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest = os.path.join(BACKUP_DIR, f"recipes_{stamp}.db")
    with get_conn() as c:
        if os.path.exists(dest):
            os.remove(dest)
        c.execute("VACUUM INTO ?", (dest,))
    # prune old backups, keep newest N
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "recipes_*.db")))
    for old in backups[:-keep]:
        try: os.remove(old)
        except OSError: pass
    return dest
