from fastapi import APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime

from ..db import all_recipes_export

router = APIRouter()

@router.get("")
async def export_all():
    """Portable JSON backup of the whole library (embeddings excluded — regenerable)."""
    recipes = all_recipes_export()
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(recipes),
        "recipes": recipes,
    }
    fname = f"recipebook_export_{datetime.now().strftime('%Y%m%d')}.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
