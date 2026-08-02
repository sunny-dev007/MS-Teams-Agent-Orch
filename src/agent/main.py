from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.agent.api import portal

app = FastAPI()

# Mount the static files directory
app.mount("/static", StaticFiles(directory="src/agent/web"), name="static")

# Include the portal router
app.include_router(portal.router)
