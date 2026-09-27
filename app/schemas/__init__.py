"""
schemas/__init__.py
Re-exports all schemas so other modules can do:
  from schemas import TaskCreate, TaskResponse
instead of the longer:
  from schemas.task import TaskCreate, TaskResponse
"""
from schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    TaskListResponse,
    TaskStatsResponse,
)

__all__ = [
    "TaskCreate",
    "TaskUpdate",
    "TaskResponse",
    "TaskListResponse",
    "TaskStatsResponse",
]
