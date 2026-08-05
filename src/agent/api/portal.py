from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os

router = APIRouter()

# Serve static files from the 'src/agent/web' directory
static_dir = os.path.join(os.path.dirname(__file__), '..', 'web')

# Mount the static files directory
router.mount('/web', StaticFiles(directory=static_dir), name='web')

@router.get('/portal')
async def get_portal():
    return FileResponse(os.path.join(static_dir, 'portal.html'))
