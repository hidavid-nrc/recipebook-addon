import os, json, math, httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

_anthropic = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY",""))
_openai    = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY",""))
MODEL      = "claude-sonnet-4-20250514"
EMBED_MODEL= "text-embedding-3-small"

# ── Embeddings ───────────────────────────────────────────────
async def embed(text: str) -> list[float]:
    r = await _openai.embeddings.create(model=EMBED_MODEL, input=text[:8000])
    return r.data[0].embedding

def cosine(a, b) -> float:
    dot = sum(x*y for x,y in zip(a,b))
    na  = math.sqrt(sum(x*x for x in a))
    nb  = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na and nb else 0.0

def recipe_text(r: dict) -> str:
    d = r.get("data", r)
    parts = [d.get("name",""), d.get("source","") or ""]
    parts += d.get("tags",[])
    if d.get("subtitle"): parts.append(d["subtitle"])
    for g in d.get("ingredientGroups",[]):
        if g.get("name"): parts.append(g["name"])
        for i in g.get("ingredients",[]): parts.append(i.get("food",""))
    return " · ".join(p for p in parts if p)

# ── Claude helper ─────────────────────────────────────────────
async def claude(system: str, user: str, max_tokens: int = 4096) -> str:
    msg = await _anthropic.messages.create(
        model=MODEL, max_tokens=max_tokens,
        system=system,
        messages=[{"role":"user","content":user}]
    )
    return msg.content[0].text.strip()

def _json(text: str):
    clean = text.lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(clean)

# ── URL scrape ───────────────────────────────────────────────
RECIPE_SCHEMA = """{
  "schemaVersion":"1","slug":"url-safe-slug","name":"Recipe Name",
  "source":"Website","subtitle":null,"note":null,"yield":"4 servings",
  "prepTime":"PT20M","cookTime":"PT30M","totalTime":"PT50M",
  "meta":[{"label":"string","value":"string"}],
  "ingredientGroups":[{"name":null,"ingredients":[
    {"raw":"2 tbsp olive oil","quantity":2.0,"unit":"tbsp","food":"olive oil","note":null,"display":"2 tbsp olive oil"}
  ]}],
  "instructions":[{"step":1,"text":"Step text","timer":null}],
  "tags":[],"linkedRecipes":[]
}"""

SCRAPE_SYS = f"""Extract all recipes from webpage text. Return a JSON array using this schema per recipe:
{RECIPE_SCHEMA}
Rules: metric units only (ml,g,kg,°C). Infer tags (asian,beef,spicy,slow-cook,vegetarian etc).
Return ONLY the JSON array, no markdown."""

async def scrape_url(url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script","style","nav","footer","header","aside"]): t.decompose()
    text = soup.get_text(separator="\n", strip=True)[:40000]
    raw = await claude(SCRAPE_SYS, f"URL: {url}\n\n{text}")
    return _json(raw)

# ── HTML parse ───────────────────────────────────────────────
HTML_SYS = f"""Extract recipes from canonical recipe HTML (article.recipe, .recipe-title, .ingredient etc).
Return a JSON array using this schema per recipe:
{RECIPE_SCHEMA}
Return ONLY the JSON array, no markdown."""

async def parse_html(html: str) -> list[dict]:
    raw = await claude(HTML_SYS, html[:40000])
    return _json(raw)

# ── Preferences ──────────────────────────────────────────────
PREFS_SYS = """Extract cooking preferences from freeform text. Return ONLY this JSON:
{"loves":[],"dislikes":[],"avoids":[],"dietary":[],"flavor_profile":[],
 "context":"one sentence","typical_servings":2}"""

async def extract_prefs(text: str) -> dict:
    raw = await claude(PREFS_SYS, text, max_tokens=1024)
    return _json(raw)

# ── Semantic search ──────────────────────────────────────────
async def semantic_search(query: str, recipes: list, prefs: dict, top_k: int = 8) -> list:
    candidates = [r for r in recipes if r.get("embedding")]
    if not candidates:
        return recipes[:top_k]
    q_vec = await embed(query)
    scored = sorted(candidates, key=lambda r: cosine(q_vec, r["embedding"]), reverse=True)[:top_k*2]
    catalog = [{"slug":r["slug"],"name":r["name"],"tags":r.get("tags",[])} for r in scored]
    rerank_prompt = f"""Query: "{query}"
Preferences: {json.dumps(prefs)}
Candidates: {json.dumps(catalog)}
Re-rank by relevance. Return JSON array: [{{"slug":"...","reason":"..."}}]
Return ONLY the JSON array."""
    raw = await claude("You are a recipe recommendation engine.", rerank_prompt, max_tokens=1024)
    reranked = _json(raw)
    slug_map = {r["slug"]: r for r in scored}
    result = []
    for item in reranked[:top_k]:
        r = slug_map.get(item["slug"])
        if r:
            r = dict(r); r["_reason"] = item.get("reason","")
            result.append(r)
    return result

# ── Meal plan generation ──────────────────────────────────────
async def gen_meal_plan(recipes: list, prefs: dict, recent: list, week_start: str) -> list:
    catalog = [{"slug":r["slug"],"name":r["name"],"tags":r.get("tags",[])} for r in recipes]
    prompt = f"""Generate a 7-day dinner plan for week starting {week_start}.
Preferences: {json.dumps(prefs)}
Recently cooked (avoid): {json.dumps(recent)}
Available: {json.dumps(catalog)}
Return JSON array: [{{"day":0,"slot":"dinner","recipe_slug":"...","servings":2,"note":"why"}}]
day 0=Monday..6=Sunday. Return ONLY the JSON array."""
    raw = await claude("You are a meal planning assistant.", prompt, max_tokens=2048)
    return _json(raw)

# ── Gap analysis ─────────────────────────────────────────────
async def gap_analysis(recipes: list, prefs: dict) -> str:
    catalog = [{"name":r["name"],"tags":r.get("tags",[])} for r in recipes]
    prompt = f"""Analyze this recipe collection against user preferences.
Preferences: {json.dumps(prefs)}
Collection ({len(catalog)} recipes): {json.dumps(catalog)}
Identify: what's well-covered, key gaps, specific recipe suggestions with sources.
Be concrete and actionable."""
    return await claude("You are a culinary advisor.", prompt, max_tokens=2048)

# ── Whisper voice transcription ──────────────────────────────
async def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Transcribe audio using OpenAI Whisper."""
    import io
    ext = "webm" if "webm" in mime_type else "mp4" if "mp4" in mime_type else "wav"
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"recording.{ext}"
    transcript = await _openai.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language="en"
    )
    return transcript.text

# ── Bring! / HA todo push ────────────────────────────────────
async def push_to_ha_todo(items: list[str], entity_id: str):
    # SUPERVISOR_TOKEN is auto-injected by HA Supervisor when
    # homeassistant_api: true is set in config.yaml
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise ValueError("SUPERVISOR_TOKEN not available — ensure homeassistant_api: true in config.yaml")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=15) as client:
        for item in items:
            resp = await client.post(
                "http://supervisor/core/api/services/todo/add_item",
                headers=headers,
                json={"entity_id": entity_id, "item": item}
            )
            resp.raise_for_status()
