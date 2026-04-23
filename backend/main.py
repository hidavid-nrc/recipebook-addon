from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from .db import init_db
from .routers import recipes, ingest, planner, preferences, voice

# HA ingress injects X-Ingress-Path header — we use it as root_path
# so all /api/... calls work correctly from the browser under ingress
app = FastAPI(title="Recipe Book", version="0.3.0")

@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    ingress_path = request.headers.get("X-Ingress-Path", "")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)

@app.on_event("startup")
async def startup():
    init_db()

app.include_router(recipes.router,     prefix="/api/recipes")
app.include_router(ingest.router,      prefix="/api/ingest")
app.include_router(planner.router,     prefix="/api/planner")
app.include_router(preferences.router, prefix="/api/preferences")
app.include_router(voice.router,       prefix="/api/voice")

FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND / "static")), name="static")

@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa(full_path: str = ""):
    return HTMLResponse((FRONTEND / "index.html").read_text())
