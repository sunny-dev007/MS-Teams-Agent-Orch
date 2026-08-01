"""
Sample Personal Task API — intentionally incomplete for WhatsApp agent E2E tests.

WhatsApp test prompts (after selecting this repo):
1. "Add DELETE /tasks/{id} endpoint"
2. "Fix title validation so empty titles are rejected with 400"
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
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
    return {"status": "ok"}


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


# TODO(agent-e2e): Missing DELETE /tasks/{id} — ask the WhatsApp agent to add it.
