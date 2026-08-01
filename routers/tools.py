from fastapi import APIRouter
from services.tools import TOOL_CATALOG

router = APIRouter(tags=["tools"])


@router.get("/api/tools")
async def list_tools():
    return list(TOOL_CATALOG.values())
