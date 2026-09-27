from sqlalchemy import UUID, String, Integer, Float,  JSON, Text, DateTime, Enum as SAEnum, ForeignKey
from .core import Base
from typing import Optional
import uuid
import datetime
from datetime import datetime, timezone
import enum
from sqlalchemy.orm import Mapped, mapped_column, relationship


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNNING = "running"
    FAILED = "failed"
    FINISHED = "finished"
    CANCELED = "cancelled"

class TaskPriority(str, enum.Enum):
    HIGH= "high"
    LOW = "low"
    NORMAL = "normal"

class TaskType(str, enum.Enum):
    GENERIC = "generic"
    DATA_EXPORT = "data_export"
    REPORT_GENERATE ="report_generate"
    SEND_EMAIL = "send_email"
    IMAGE_PROCESS = "image_process"

class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    FirstName: Mapped[str] = mapped_column(
        String, nullable=False
    )
    LastName: Mapped[str] = mapped_column(
        String, nullable=False
    )
    MiddleName: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    email: Mapped[str] = mapped_column(
        String, index=True, unique=True, nullable=False
    )

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates='owner')

class Task(Base):
    __tablename__ = "task"
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),  primary_key=True
    )

    title: Mapped[str] = mapped_column(
        String, nullable=False
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=True
    )
    status: Mapped[TaskType] = mapped_column(
        SAEnum(TaskType, values_callable=lambda x: [e.Value for e in x]), nullable=False, default=TaskType.GENERIC
    )
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority, values_callable=lambda x: [e.Value for e in x]), nullable=False, default=TaskPriority.NORMAL
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    result : Mapped[str] = mapped_column(
        JSON, nullable=True
    )
    error_message: Mapped[str] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    owner: Mapped[Optional["User"]] = relationship("User", back_populates="tasks")

    def __repr__(self) -> str:
            return f"<Task id={self.id} title='{self.title}' status={self.status}>"