from fastapi import APIRouter
from fastapi.responses import JSONResponse
from db import get_pool
from config import settings

router = APIRouter()


@router.get("/api/health")
async def health():
    checks = {"db": False, "anthropic": False, "openai": False}
    ok = True

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["db"] = True
    except Exception:
        ok = False

    checks["anthropic"] = bool(settings.anthropic_api_key)
    checks["openai"] = bool(settings.openai_api_key)

    if not checks["anthropic"] or not checks["openai"]:
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=status_code)
