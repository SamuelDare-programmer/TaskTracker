from pydantic import BaseModel, Field
from typing import Optional, Any
from ..db.model import TaskPriority, TaskStatus, TaskType
import uuid
from datetime import datetime


class TaskBase(BaseModel):
    title : str = Field()
    description: Optional[str] = Field()
    priority: TaskPriority = Field(default=TaskPriority.NORMAL)
    type: TaskType = Field(default=TaskType.GENERIC)


class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    title: Optional[str] = Field()
    description: Optional[str] = Field()
    priority: Optional[TaskPriority] = Field()
    type: Optional[TaskType] = Field()


class TaskResponse(TaskBase):
    model_config = {"from_attributes":True}

    id: uuid.UUID
    status: TaskStatus
    progress: int = Field()
    result: Optional[Any] = None
    error_message: Optional[Any] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    owner_id: Optional[uuid.UUID] = None
