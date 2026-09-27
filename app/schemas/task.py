"""
schemas/task.py — Pydantic Schemas for the Task API
─────────────────────────────────────────────────────────────────────────────
Schemas are NOT the same as database models.

  DB Model  (db/models.py)  → defines what's stored in the database
  Schema    (this file)     → defines what the API accepts / returns

WHY SEPARATE THEM?
  - You might store a hashed password in the DB but never want to return it
    in an API response.
  - You might accept only `title` when creating a task, but return `id`,
    `status`, `created_at`, etc. in the response.
  - Pydantic validates and coerces data automatically — no manual if-checks.

SCHEMA HIERARCHY:
  TaskBase          ← shared fields (avoids repeating yourself)
    └── TaskCreate  ← what the client POSTs (input)
    └── TaskUpdate  ← what the client PATCHes (partial update)
    └── TaskResponse← what the API returns (output — richer than input)

  TaskListResponse  ← paginated wrapper around a list of TaskResponse
  TaskStatsResponse ← task counts grouped by status
─────────────────────────────────────────────────────────────────────────────
"""

import uuid
from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field, field_validator

from db.models import TaskStatus, TaskPriority, TaskType


# ────────────────────────────────────────────────────────────────────────────
# BASE SCHEMA  (shared fields inherited by Create & Response)
# ────────────────────────────────────────────────────────────────────────────

class TaskBase(BaseModel):
    """
    Fields that are shared across create and response schemas.
    Putting them here avoids duplicating the definitions.
    """
    title: str = Field(
        ...,                          # `...` means "required, no default"
        min_length=1,
        max_length=255,
        description="A short name for the task",
        examples=["Export user report"],
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional longer explanation of what this task does",
        examples=["Exports all users created in the last 30 days to CSV"],
    )
    task_type: TaskType = Field(
        default=TaskType.GENERIC,
        description="The kind of work this task performs",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.NORMAL,
        description="Queue priority — high priority tasks run first",
    )


# ────────────────────────────────────────────────────────────────────────────
# CREATE SCHEMA  (POST /tasks body)
# ────────────────────────────────────────────────────────────────────────────

class TaskCreate(TaskBase):
    """
    Schema for creating a new task.
    Only fields the CLIENT provides — the server sets id, status, timestamps.

    Example request body:
        {
            "title": "Export Q4 Report",
            "task_type": "report_generate",
            "priority": "high"
        }
    """
    # `metadata` lets the caller attach arbitrary key-value data to the task.
    # The worker can read this dict and use it (e.g., {"user_id": 42, "format": "pdf"})
    metadata: Optional[dict] = Field(
        default=None,
        description="Optional arbitrary data passed to the task executor",
        examples=[{"format": "pdf", "include_charts": True}],
    )


# ────────────────────────────────────────────────────────────────────────────
# UPDATE SCHEMA  (PATCH /tasks/{id} body)
# ────────────────────────────────────────────────────────────────────────────

class TaskUpdate(BaseModel):
    """
    Schema for partially updating a task.
    ALL fields are Optional so the client can send only what they want to change.

    Pydantic's `model_config = {"..." }` with populate_by_name lets us accept
    both camelCase and snake_case if needed.
    """
    title: Optional[str]       = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    priority: Optional[TaskPriority] = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, v: Optional[str]) -> Optional[str]:
        """Custom validation: if title is provided, it can't be just whitespace."""
        if v is not None and v.strip() == "":
            raise ValueError("title cannot be blank")
        return v


# ────────────────────────────────────────────────────────────────────────────
# RESPONSE SCHEMA  (what the API returns)
# ────────────────────────────────────────────────────────────────────────────

class TaskResponse(TaskBase):
    """
    Schema for a task in API responses.
    Extends TaskBase with server-generated fields (id, status, timestamps, etc.)

    `model_config = {"from_attributes": True}` is CRITICAL — it tells Pydantic
    to read values from SQLAlchemy ORM object attributes (like task.id, task.status)
    instead of from a plain dict. Without this, TaskResponse.model_validate(task_orm)
    would fail because ORM objects aren't dicts.
    """
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: TaskStatus
    progress: int = Field(ge=0, le=100, description="Completion percentage 0–100")
    result: Optional[Any]    = None   # whatever the executor returned
    error_message: Optional[str] = None

    # Timestamps — the API always returns UTC ISO-8601 strings
    created_at:   datetime
    updated_at:   datetime
    started_at:   Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Optional — only present if task has an owner
    owner_id: Optional[uuid.UUID] = None


# ────────────────────────────────────────────────────────────────────────────
# LIST RESPONSE  (GET /tasks)
# ────────────────────────────────────────────────────────────────────────────

class TaskListResponse(BaseModel):
    """
    Wraps a list of tasks with pagination metadata.

    Example response:
        {
            "tasks": [...],
            "total": 42,
            "page": 1,
            "page_size": 10,
            "has_more": true
        }
    """
    tasks:     List[TaskResponse]
    total:     int  = Field(description="Total number of tasks matching the filter")
    page:      int  = Field(description="Current page number (1-based)")
    page_size: int  = Field(description="Number of items per page")
    has_more:  bool = Field(description="True if there are more pages")


# ────────────────────────────────────────────────────────────────────────────
# STATS RESPONSE  (GET /tasks/stats)
# ────────────────────────────────────────────────────────────────────────────

class TaskStatsResponse(BaseModel):
    """
    A snapshot of task counts grouped by status.
    Useful for building a dashboard or health check.

    Example response:
        {
            "pending": 3,
            "running": 1,
            "completed": 14,
            "failed": 2,
            "cancelled": 0,
            "total": 20
        }
    """
    pending:   int = 0
    running:   int = 0
    completed: int = 0
    failed:    int = 0
    cancelled: int = 0
    total:     int = 0
