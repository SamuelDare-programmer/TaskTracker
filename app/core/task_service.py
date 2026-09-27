"""
core/task_service.py — Business Logic Layer
─────────────────────────────────────────────────────────────────────────────
This module contains ALL the business logic for tasks.

WHY A SERVICE LAYER?
  Routes (routes/tasks.py) should only handle HTTP concerns:
    - Parse request body
    - Call a service function
    - Return a response

  All the "what do we actually do?" logic lives HERE.
  This makes the code easier to test — you can test service functions
  without running an HTTP server.

  ROUTE → SERVICE → DB (and optionally → WORKER / MANAGER)
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Task, TaskStatus, TaskPriority
from schemas.task import TaskCreate, TaskUpdate, TaskStatsResponse

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# CREATE
# ────────────────────────────────────────────────────────────────────────────

async def create_task(
    data: TaskCreate,
    db: AsyncSession,
    worker,                  # TaskWorker instance (avoids circular imports)
    owner_id: Optional[UUID] = None,
) -> Task:
    """
    Create a new task in the database and enqueue it for processing.

    Steps:
      1. Build a Task ORM object from the validated Pydantic schema.
      2. Insert it into the DB (status = PENDING).
      3. Add its ID to the worker's queue.
      4. Return the saved Task object.

    Args:
        data:     Validated TaskCreate schema (from request body).
        db:       The DB session (injected by FastAPI's Depends).
        worker:   The TaskWorker singleton (to enqueue the task).
        owner_id: Optional UUID of the authenticated user.
    """
    # Build the Task ORM object.
    # model_dump() converts the Pydantic model to a plain dict.
    # We exclude None values so DB defaults take over for unset fields.
    task_data = data.model_dump(exclude_none=True)

    # `metadata` is stored in the result field initially (the worker can read it)
    # We don't have a dedicated metadata column — we store it in `result`.
    metadata = task_data.pop("metadata", None)

    task = Task(
        **task_data,
        owner_id=owner_id,
        # Store the input metadata in result so the executor can access it
        result={"input_metadata": metadata} if metadata else None,
    )

    db.add(task)
    await db.commit()
    await db.refresh(task)   # Reload from DB so we have server-generated fields (id, timestamps)

    logger.info(f"Task created: {task.id} (type={task.task_type}, priority={task.priority})")

    # Enqueue the task ID (not the whole object) for background processing
    await worker.enqueue(str(task.id))

    return task


# ────────────────────────────────────────────────────────────────────────────
# READ
# ────────────────────────────────────────────────────────────────────────────

async def get_task(task_id: str, db: AsyncSession) -> Optional[Task]:
    """
    Fetch a single task by its UUID.
    Returns None if no task with that ID exists.
    """
    return await db.get(Task, task_id)


async def list_tasks(
    db: AsyncSession,
    status: Optional[TaskStatus] = None,
    priority: Optional[TaskPriority] = None,
    page: int = 1,
    page_size: int = 10,
) -> tuple[list[Task], int]:
    """
    Fetch a paginated, optionally filtered list of tasks.

    Returns a tuple of (tasks_for_this_page, total_matching_count).

    HOW PAGINATION WORKS:
      page=1, page_size=10 → rows 1–10
      page=2, page_size=10 → rows 11–20
      SQL: LIMIT 10 OFFSET (page-1)*page_size
    """
    # Build up the WHERE conditions dynamically
    filters = []
    if status is not None:
        filters.append(Task.status == status)
    if priority is not None:
        filters.append(Task.priority == priority)

    # ── Count query — how many rows match in total? ────────────────────────
    count_stmt = select(func.count()).select_from(Task)
    if filters:
        count_stmt = count_stmt.where(and_(*filters))

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # ── Data query — fetch the actual rows for this page ──────────────────
    offset = (page - 1) * page_size

    data_stmt = (
        select(Task)
        .order_by(Task.created_at.desc())   # newest first
        .offset(offset)
        .limit(page_size)
    )
    if filters:
        data_stmt = data_stmt.where(and_(*filters))

    result = await db.execute(data_stmt)
    tasks = list(result.scalars().all())   # .scalars() extracts Task objects from the result

    return tasks, total


# ────────────────────────────────────────────────────────────────────────────
# UPDATE
# ────────────────────────────────────────────────────────────────────────────

async def update_task(task_id: str, data: TaskUpdate, db: AsyncSession) -> Optional[Task]:
    """
    Apply a partial update to a task.
    Only fields that are NOT None in the update schema are changed.

    Example: TaskUpdate(title="New title") only changes the title.
    """
    task = await db.get(Task, task_id)
    if task is None:
        return None

    # model_dump(exclude_none=True) gives us only the fields the client set
    update_fields = data.model_dump(exclude_none=True)

    for field, value in update_fields.items():
        setattr(task, field, value)   # dynamically update the ORM attribute

    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ────────────────────────────────────────────────────────────────────────────
# CANCEL
# ────────────────────────────────────────────────────────────────────────────

async def cancel_task(task_id: str, db: AsyncSession) -> Optional[Task]:
    """
    Cancel a task IF it is still pending (hasn't started yet).

    We can't cancel a task that's already running — the executor would need
    a cancellation mechanism (asyncio.Task.cancel()) which is more complex.
    For now, we only allow cancelling PENDING tasks.
    """
    task = await db.get(Task, task_id)
    if task is None:
        return None

    if task.status != TaskStatus.PENDING:
        # Return the task as-is; the route will return a 409 Conflict
        return task

    task.status = TaskStatus.CANCELLED
    db.add(task)
    await db.commit()
    await db.refresh(task)

    logger.info(f"Task {task_id} cancelled by user")
    return task


# ────────────────────────────────────────────────────────────────────────────
# STATS
# ────────────────────────────────────────────────────────────────────────────

async def get_task_stats(db: AsyncSession) -> TaskStatsResponse:
    """
    Count tasks grouped by status.

    Uses a single SQL query with GROUP BY instead of 5 separate COUNT queries.
    This is more efficient because it hits the DB only once.

    SQL equivalent:
        SELECT status, COUNT(*) FROM tasks GROUP BY status;
    """
    stmt = (
        select(Task.status, func.count(Task.id).label("count"))
        .group_by(Task.status)
    )
    result = await db.execute(stmt)
    rows = result.all()   # list of (status, count) tuples

    # Build the response dict, defaulting all statuses to 0
    stats = {status.value: 0 for status in TaskStatus}
    total = 0
    for status, count in rows:
        stats[status.value] = count
        total += count

    return TaskStatsResponse(
        pending=stats.get("pending", 0),
        running=stats.get("running", 0),
        completed=stats.get("completed", 0),
        failed=stats.get("failed", 0),
        cancelled=stats.get("cancelled", 0),
        total=total,
    )
