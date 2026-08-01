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
    # Ensure ORM models are registered before create_all
    from agent.core import session as _session_models  # noqa: F401
    from agent.models import task as _task_models  # noqa: F401

    await init_db()

    from agent.core.logging import get_logger
    from agent.services.whatsapp import check_access_token

    log = get_logger(__name__)
    token_status = await check_access_token()
    if token_status.get("ok"):
        log.info(
            "WhatsApp token OK (%s)",
            token_status.get("display_phone_number") or token_status.get("phone_number_id"),
        )
    else:
        log.error(
            "WhatsApp token check failed at startup: %s — replies will not be delivered until "
            "WHATSAPP_ACCESS_TOKEN is replaced with a long-lived or System User token.",
            token_status,
        )

    try:
        yield
    finally:
        from agent.agents.graph import close_graph_resources

        await close_graph_resources()


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
