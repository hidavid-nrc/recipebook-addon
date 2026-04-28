from fastapi import APIRouter, Request, Response
import httpx
import os

router = APIRouter()
ANTHROPIC_BASE = "https://api.anthropic.com"

@router.api_route("/anthropic/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def anthropic_proxy(path: str, request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, x-api-key, anthropic-version, anthropic-beta",
        })
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    body = await request.body()
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
        "x-api-key": request.headers.get("x-api-key", api_key),
    }
    if "anthropic-beta" in request.headers:
        headers["anthropic-beta"] = request.headers["anthropic-beta"]
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.request(method=request.method,
            url=f"{ANTHROPIC_BASE}/{path}", headers=headers, content=body)
    return Response(content=resp.content, status_code=resp.status_code, headers={
        "Content-Type": resp.headers.get("content-type", "application/json"),
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
    })
