"""
Sample Personal Task API — WhatsApp agent E2E demo app.

Browser: https://personal-task-api-sunny.azurewebsites.net/
Docs:    https://personal-task-api-sunny.azurewebsites.net/docs

WhatsApp prompts (after selecting this GitHub repo):
1. Add DELETE /tasks/{id}
2. Reject empty titles with HTTP 400
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Personal Task API", version="0.1.0")

_TASKS: dict[int, dict] = {}
_NEXT_ID = 1


class TaskCreate(BaseModel):
    title: str = Field(..., description="Task title")
    done: bool = False


class Task(TaskCreate):
    id: int


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    items = "".join(
        f"<li><b>#{t['id']}</b> {t['title']} "
        f"{'✅' if t['done'] else '🕒'}</li>"
        for t in _TASKS.values()
    ) or "<li><i>No tasks yet — use /docs or WhatsApp agent to add features</i></li>"
    return f"""<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Personal Task API</title>
<style>
body{{font-family:system-ui,sans-serif;margin:1.25rem;background:#0f172a;color:#e2e8f0}}
a{{color:#38bdf8}} .card{{background:#1e293b;padding:1rem;border-radius:12px;margin:.75rem 0}}
code{{background:#334155;padding:.1rem .35rem;border-radius:6px}}
</style></head><body>
<h1>Personal Task API</h1>
<p>Sunny's WhatsApp agent demo app. Open <a href="/docs">/docs</a> to try APIs.</p>
<div class="card">
<h3>Current tasks</h3>
<ul>{items}</ul>
</div>
<div class="card">
<p><b>Intentional gaps for WhatsApp testing:</b></p>
<ul>
<li>No <code>DELETE /tasks/{{id}}</code> yet</li>
<li>Empty titles are accepted (should return 400)</li>
</ul>
</div>
</body></html>"""


@app.get("/tasks", response_model=list[Task])
def list_tasks() -> list[Task]:
    return [Task(**t) for t in _TASKS.values()]


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(payload: TaskCreate) -> Task:
    global _NEXT_ID
    # INTENTIONAL BUG for agent testing: empty titles are accepted
    task = {"id": _NEXT_ID, "title": payload.title, "done": payload.done}
    _TASKS[_NEXT_ID] = task
    _NEXT_ID += 1
    return Task(**task)


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
    task = _TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task(**task)


# TODO(agent-e2e): Missing DELETE /tasks/{id} — ask WhatsApp agent to add it.
