from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from src.agent.config import settings
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="src/web")

@router.get("/portal", response_class=HTMLResponse)
async def get_portal(request: Request):
    return templates.TemplateResponse("portal.html", {"request": request, "version": settings.version})
