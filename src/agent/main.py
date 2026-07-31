from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class NgrokBypassMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response


from agent.api.gmail import router as gmail_router
from agent.api.health import router as health_router
from agent.api.tasks import router as tasks_router
from agent.api.whatsapp import router as whatsapp_router
from agent.core.logging import setup_logging
from agent.models.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    yield


app = FastAPI(
    title="My Personal AI Agent",
    description="AI-driven multi-agent platform with WhatsApp interface",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(NgrokBypassMiddleware)

app.include_router(health_router)
app.include_router(whatsapp_router)
app.include_router(gmail_router)
app.include_router(tasks_router)
