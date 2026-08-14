import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from .db import init_db, backup_db
from .routers import recipes, ingest, planner, preferences, voice, proxy, epub, export, youtube, pdf_import, admin

app = FastAPI(title="Recipe Book", version="0.6.5")

@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    # HA ingress injects X-Ingress-Path; expose it as root_path so FastAPI
    # generates correct URLs. The frontend also prefixes fetches with BASE.
    ingress_path = request.headers.get("X-Ingress-Path", "")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)

async def _daily_backup():
    while True:
        try:
            path = backup_db()
            if path:
                print(f"[backup] wrote {path}")
        except Exception as e:
            print(f"[backup] failed: {e}")
        await asyncio.sleep(24 * 3600)

@app.on_event("startup")
async def startup():
    init_db()
    try:
        backup_db()            # one backup at boot
    except Exception as e:
        print(f"[backup] startup failed: {e}")
    asyncio.create_task(_daily_backup())

# API routers FIRST so none are shadowed by the SPA catch-all below.
app.include_router(recipes.router,     prefix="/api/recipes")
app.include_router(ingest.router,      prefix="/api/ingest")
app.include_router(epub.router,        prefix="/api/ingest")
app.include_router(planner.router,     prefix="/api/planner")
app.include_router(preferences.router, prefix="/api/preferences")
app.include_router(voice.router,       prefix="/api/voice")
app.include_router(proxy.router,       prefix="/api/proxy")
app.include_router(export.router,      prefix="/api/export")
app.include_router(youtube.router,     prefix="/api/ingest")
app.include_router(pdf_import.router,  prefix="/api/ingest")
app.include_router(admin.router,       prefix="/api/admin")

FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND / "static")), name="static")

@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa(full_path: str = ""):
    # Never let the SPA swallow unmatched API calls.
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    html = (FRONTEND / "index.html").read_text()
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
