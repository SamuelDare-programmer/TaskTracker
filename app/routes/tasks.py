"""
routes/tasks.py — REST API Endpoints for Tasks
─────────────────────────────────────────────────────────────────────────────
This file defines the HTTP routes (endpoints) for the task management API.

DESIGN PHILOSOPHY — "Thin Routes":
  Routes should be as thin as possible. Their job is:
    1. Parse and validate the incoming request (Pydantic does this automatically)
    2. Call the appropriate service function
    3. Return the appropriate HTTP response

  All business logic lives in core/task_service.py, NOT here.

FASTAPI CONCEPTS USED:
  - APIRouter   → groups related routes (we mount it in main.py)
  - Depends()   → dependency injection (auto-provides db session, worker)
  - HTTPException → returns proper HTTP error codes (404, 409, etc.)
  - Response status codes → 200 OK, 201 Created, 204 No Content, etc.

ENDPOINT SUMMARY:
  GET    /tasks/stats        → task counts by status
  POST   /tasks              → create & enqueue a new task
  GET    /tasks              → paginated list (filterable by status/priority)
  GET    /tasks/{task_id}    → single task details
  PATCH  /tasks/{task_id}    → partial update (title, description, priority)
  DELETE /tasks/{task_id}    → cancel a pending task
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.core import get_db_session
from db.models import TaskStatus, TaskPriority
from schemas.task import TaskCreate, TaskUpdate, TaskResponse, TaskListResponse, TaskStatsResponse
from core import task_worker                          # The singleton TaskWorker
import core.task_service as svc                      # Business logic functions

logger = logging.getLogger(__name__)

# ── Router setup ───────────────────────────────────────────────────────────────
# prefix="/tasks" means every route below is at /tasks/...
# tags=["Tasks"] groups them together in the Swagger UI at /docs
router = APIRouter(prefix="/tasks", tags=["Tasks"])


# ── GET /tasks/stats  (must come BEFORE /{task_id} to avoid conflict) ─────────
@router.get(
    "/stats",
    response_model=TaskStatsResponse,
    summary="Get task statistics",
    description="Returns a count of tasks grouped by status (pending, running, completed, etc.)",
)
async def get_stats(db: AsyncSession = Depends(get_db_session)):
    """
    Dashboard endpoint — shows how many tasks are in each state.

    Why is /stats BEFORE /{task_id}?
      FastAPI matches routes in order. If /{task_id} came first,
      a request to /tasks/stats would treat "stats" as a task_id and fail.
    """
    return await svc.get_task_stats(db)


# ── POST /tasks ────────────────────────────────────────────────────────────────
@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,   # 201 = "resource was created"
    summary="Create a new task",
    description="Creates a task, saves it to the database, and enqueues it for background processing.",
)
async def create_task(
    data: TaskCreate,                          # FastAPI auto-validates the JSON body
    db: AsyncSession = Depends(get_db_session),
):
    """
    Create and enqueue a background task.

    The task starts as PENDING. The background worker will pick it up
    and transition it through RUNNING → COMPLETED (or FAILED).

    Connect to WS /ws/tasks/{task_id} to receive live progress updates.
    """
    task = await svc.create_task(data=data, db=db, worker=task_worker)
    return task


# ── GET /tasks ─────────────────────────────────────────────────────────────────
@router.get(
    "",
    response_model=TaskListResponse,
    summary="List all tasks",
    description="Returns a paginated list of tasks. Filter by status or priority.",
)
async def list_tasks(
    # Query parameters — e.g., GET /tasks?status=pending&page=2&page_size=5
    # Query(...) registers these as optional query params with docs in Swagger
    status_filter: Optional[TaskStatus]   = Query(default=None, alias="status",
                                                   description="Filter by task status"),
    priority: Optional[TaskPriority]      = Query(default=None,
                                                   description="Filter by priority level"),
    page: int                             = Query(default=1, ge=1,
                                                   description="Page number (starts at 1)"),
    page_size: int                        = Query(default=10, ge=1, le=100,
                                                   description="Items per page (max 100)"),
    db: AsyncSession = Depends(get_db_session),
):
    tasks, total = await svc.list_tasks(
        db=db,
        status=status_filter,
        priority=priority,
        page=page,
        page_size=page_size,
    )
    return TaskListResponse(
        tasks=tasks,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,  # True if there are more pages
    )


# ── GET /tasks/{task_id} ───────────────────────────────────────────────────────
@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Get a task by ID",
    description="Returns the full details of a single task, including its current status and result.",
)
async def get_task(
    task_id: str,                              # Path parameter — from the URL
    db: AsyncSession = Depends(get_db_session),
):
    task = await svc.get_task(task_id, db)
    if task is None:
        # 404 Not Found — standard REST convention for missing resources
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id '{task_id}' not found",
        )
    return task


# ── PATCH /tasks/{task_id} ─────────────────────────────────────────────────────
@router.patch(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Partially update a task",
    description="Update a task's title, description, or priority. Only provided fields are changed.",
)
async def update_task(
    task_id: str,
    data: TaskUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """
    PATCH vs PUT:
      PUT   → replace the entire resource
      PATCH → update only the fields you send

    We use PATCH here because tasks have many auto-managed fields (status,
    progress, timestamps) that the client should never touch directly.
    """
    task = await svc.update_task(task_id, data, db)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id '{task_id}' not found",
        )
    return task


# ── DELETE /tasks/{task_id} ────────────────────────────────────────────────────
@router.delete(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Cancel a task",
    description="Cancels a PENDING task. Running, completed, or failed tasks cannot be cancelled.",
)
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    We use DELETE semantics because the user is "removing" it from the queue.
    But we return the updated task (instead of 204 No Content) so the client
    can see the new status=cancelled.
    """
    task = await svc.cancel_task(task_id, db)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with id '{task_id}' not found",
        )

    if task.status != TaskStatus.CANCELLED:
        # Task exists but is not cancellable in its current state
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,   # 409 = state conflict
            detail=f"Cannot cancel task in status '{task.status.value}'. "
                   f"Only PENDING tasks can be cancelled.",
        )

    return task
