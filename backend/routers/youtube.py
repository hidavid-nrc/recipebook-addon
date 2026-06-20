# backend/routers/youtube.py — YouTube recipe import via transcript + description
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import re, logging

from ..llm import claude, _json, MODEL_FAST
from ..db import upsert_recipe, get_recipe
from .recipes import _embed

logger = logging.getLogger(__name__)
router = APIRouter()

class YouTubeIn(BaseModel):
    url: str

def extract_video_id(url: str) -> str | None:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

async def fetch_transcript(video_id: str) -> str | None:
    """Fetch YouTube auto-generated or manual transcript."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id)
        return " ".join(entry.text for entry in transcript)
    except Exception as e:
        logger.warning(f"Transcript unavailable for {video_id}: {e}")
        return None

async def fetch_description(video_id: str) -> str | None:
    """Fetch video description from the YouTube page."""
    import httpx
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
            # Description is in a meta tag or in the page's JSON
            text = r.text
            # Try og:description first
            m = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', text)
            if m and len(m.group(1)) > 50:
                return m.group(1)
            # Try the shortDescription from ytInitialPlayerResponse
            m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', text)
            if m:
                desc = m.group(1).replace("\\n", "\n").replace('\\"', '"')
                if len(desc) > 50:
                    return desc
    except Exception as e:
        logger.warning(f"Description fetch failed for {video_id}: {e}")
    return None

def make_slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9\s-]', '', name.lower())
    s = re.sub(r'[\s-]+', '-', s).strip('-')
    return s[:80]

YOUTUBE_SYS = """Extract ALL recipes from this YouTube video transcript and/or description.
Return a JSON array. Each recipe object:
{
  "name": "Recipe Name",
  "description": "One-sentence description",
  "source": "YouTube — Channel Name",
  "subtitle": "recipe",
  "yield": "Serves N" or null,
  "totalTime": "PT30M" or null,
  "prepTime": null,
  "tags": ["tag1", "tag2"],
  "ingredientGroups": [{"name": null, "ingredients": [
    {"raw": "2 tablespoons soy sauce", "quantity": 2.0, "unit": "tablespoon", "food": "soy sauce", "note": null, "display": "2 tablespoons soy sauce"}
  ]}],
  "instructions": [{"step": 1, "text": "Step text", "timer": null}]
}

Rules:
- Normalize spoken quantities to numeric ("two tablespoons" → 2.0)
- Use metric units (g, ml, °C) where possible
- Infer tags from content (asian, beef, pasta, quick, etc.)
- If the transcript is unclear on exact quantities, make reasonable estimates and add a note
- Return [] if no recipe content found
- Return ONLY the JSON array, no markdown"""

@router.post("/youtube")
async def import_youtube(body: YouTubeIn, bg: BackgroundTasks):
    video_id = extract_video_id(body.url)
    if not video_id:
        raise HTTPException(400, f"Could not extract video ID from URL: {body.url}")

    # Fetch transcript and description in parallel-ish
    transcript = await fetch_transcript(video_id)
    description = await fetch_description(video_id)

    if not transcript and not description:
        raise HTTPException(
            422,
            f"No transcript or description available for video {video_id}. "
            "The video may have captions disabled, or YouTube blocked the request."
        )

    # Build the combined input for Haiku
    parts = []
    if transcript:
        parts.append(f"VIDEO TRANSCRIPT:\n{transcript[:30000]}")
    if description:
        parts.append(f"VIDEO DESCRIPTION:\n{description[:5000]}")
    combined = "\n\n".join(parts)

    source_type = "transcript+description" if (transcript and description) else ("transcript" if transcript else "description")

    try:
        raw = await claude(YOUTUBE_SYS, combined, max_tokens=4096, model=MODEL_FAST)
        recipes = _json(raw)
        if not isinstance(recipes, list):
            recipes = [recipes] if isinstance(recipes, dict) else []
    except Exception as e:
        raise HTTPException(422, f"AI extraction failed: {str(e)[:300]}")

    if not recipes:
        raise HTTPException(422, "No recipes found in this video")

    imported = []
    for r in recipes:
        if not r.get("name"):
            continue
        slug = make_slug(r["name"])
        r["slug"] = slug
        if not r.get("source"):
            r["source"] = f"YouTube — {video_id}"
        r["note"] = f"Imported from YouTube: {body.url}\nSource: {source_type}"
        if not r.get("tags"):
            r["tags"] = []
        if "youtube" not in r["tags"]:
            r["tags"].append("youtube")

        existing = get_recipe(slug)
        if existing:
            imported.append({"name": r["name"], "status": "skipped (exists)"})
            continue

        upsert_recipe(r)
        bg.add_task(_embed, r)
        imported.append({"name": r["name"], "status": "imported"})

    return {
        "imported": len([i for i in imported if i["status"] == "imported"]),
        "skipped": len([i for i in imported if "skipped" in i["status"]]),
        "names": [i["name"] for i in imported],
        "source_type": source_type,
        "video_id": video_id,
    }
