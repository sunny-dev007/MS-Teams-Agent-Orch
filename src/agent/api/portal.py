"""Phone-friendly portal page for Azure DevOps E2E edits."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

router = APIRouter(tags=["portal"])

PORTAL_FILE = Path(__file__).resolve().parent.parent / "web" / "portal.html"


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/portal", status_code=307)


@router.get("/portal", response_class=HTMLResponse)
async def portal_page():
    if not PORTAL_FILE.exists():
        return HTMLResponse(
            "<h1>Sunny Portal</h1><p>portal.html missing from deploy package.</p>",
            status_code=500,
        )
    return FileResponse(PORTAL_FILE, media_type="text/html; charset=utf-8")
