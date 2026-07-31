from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.models.db import get_session
from agent.models.schemas import TaskResponse
from agent.models.task import Task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/", response_model=list[TaskResponse])
async def list_tasks(
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Task).order_by(Task.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Task not found")
    return task
