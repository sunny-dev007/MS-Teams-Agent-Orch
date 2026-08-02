from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Template
from src.agent.config import settings

router = APIRouter()

@router.get("/portal", response_class=HTMLResponse)
async def get_portal(request: Request):
    version = settings.VERSION
    with open("src/web/portal.html") as file:
        template = Template(file.read())
    return template.render(version=version)
