"""
db/models.py — SQLAlchemy ORM Models (Database Table Definitions)
─────────────────────────────────────────────────────────────────────────────
Each class here maps to ONE table in the database.
SQLAlchemy reads the class attributes to know the column names, types,
and constraints (primary key, unique, nullable, etc.).

MAPPED COLUMNS EXPLAINED:
  Mapped[str]              → column is NOT NULL (required)
  Mapped[Optional[str]]    → column IS NULL (optional)
  mapped_column(index=True) → creates a DB index for faster lookups
─────────────────────────────────────────────────────────────────────────────
"""

import uuid
import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, UUID, Integer, Text, JSON, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.core import Base


# ────────────────────────────────────────────────────────────────────────────
# ENUMS — Python enums that get stored as strings in the database
# ────────────────────────────────────────────────────────────────────────────

class TaskStatus(str, enum.Enum):
    """
    The lifecycle of a task:
      pending   → task is created and waiting in the queue
      running   → background worker has picked it up and is executing it
      completed → task finished successfully
      failed    → task threw an exception during execution
      cancelled → user cancelled before worker picked it up
    """
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, enum.Enum):
    """
    Priority determines the order in which tasks are processed.
    Higher priority tasks jump the queue (implemented in the worker).
    """
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"


class TaskType(str, enum.Enum):
    """
    The kind of work the task represents.
    Each type maps to a different simulated executor in core/worker.py.
    """
    GENERIC         = "generic"          # Default: sleeps for a few seconds
    DATA_EXPORT     = "data_export"      # Simulates exporting a CSV file
    SEND_EMAIL      = "send_email"       # Simulates sending an email
    IMAGE_PROCESS   = "image_process"    # Simulates resizing/converting an image
    REPORT_GENERATE = "report_generate"  # Simulates building a PDF report


# ────────────────────────────────────────────────────────────────────────────
# USER MODEL  (existing — preserved)
# ────────────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Represents an application user.
    Tasks can optionally be owned by a user via the owner_id foreign key.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    FirstName:  Mapped[str]           = mapped_column(String, nullable=False)
    LastName:   Mapped[str]           = mapped_column(String, nullable=False)
    MiddleName: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email:      Mapped[str]           = mapped_column(String, index=True, unique=True, nullable=False)

    # One user can own many tasks (one-to-many relationship)
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="owner")


# ────────────────────────────────────────────────────────────────────────────
# TASK MODEL  (new)
# ────────────────────────────────────────────────────────────────────────────

class Task(Base):
    """
    Represents a single unit of background work.

    COLUMN REFERENCE:
      id             → unique identifier (UUID), auto-generated
      title          → human-readable name for the task
      description    → optional longer explanation
      task_type      → which executor runs this task (see TaskType enum)
      status         → current lifecycle state (see TaskStatus enum)
      priority       → queue priority (see TaskPriority enum)
      progress       → integer 0–100, updated by the worker as it runs
      result         → JSON blob — the worker stores its output here
      error_message  → filled in if the task fails, with the exception message
      created_at     → when the task was created (set automatically)
      updated_at     → when any field was last modified (set automatically)
      started_at     → when the worker started executing (None until then)
      completed_at   → when the task finished or failed (None until then)
      owner_id       → FK to users.id (nullable = tasks can be anonymous)
    """
    __tablename__ = "tasks"

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,   # SQLAlchemy generates a UUID automatically
    )

    # ── Task content ──────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # SAEnum stores the string value of the Python enum in the DB column.
    # This means you'll see "pending", "running", etc. in the raw DB — readable!
    task_type: Mapped[TaskType] = mapped_column(
        SAEnum(TaskType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TaskType.GENERIC,
    )

    # ── Lifecycle state ───────────────────────────────────────────────────────
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TaskStatus.PENDING,
        index=True,   # We'll frequently filter tasks by status, so index it
    )

    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TaskPriority.NORMAL,
    )

    # ── Progress tracking ─────────────────────────────────────────────────────
    # progress goes from 0 to 100.
    # The worker updates this periodically so clients see live progress.
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # result stores arbitrary data the worker produces (dict → JSON in DB)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # filled on failure so the client knows what went wrong
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    # timezone.utc ensures times are always stored in UTC, never local time.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),  # auto-update on every write
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # owner_id is nullable → a task doesn't NEED an owner.
    # When you add auth, you'll set this to the logged-in user's ID.
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),  # if user deleted → set NULL
        nullable=True,
    )
    owner: Mapped[Optional["User"]] = relationship("User", back_populates="tasks")

    def __repr__(self) -> str:
        return f"<Task id={self.id} title='{self.title}' status={self.status}>"