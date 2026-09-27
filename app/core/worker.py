"""
core/worker.py — Background Task Processing Engine
─────────────────────────────────────────────────────────────────────────────
This is the "engine room" of the API — it runs tasks in the background
without blocking the web server from handling new requests.

HOW BACKGROUND PROCESSING WORKS (step by step):
  1. A client POSTs to /tasks → FastAPI creates a Task in the DB (status=PENDING)
     and puts the task's UUID into an asyncio.Queue.

  2. The worker coroutine (started at app startup) runs in a loop:
       while True:
           task_id = await queue.get()   ← waits here if queue is empty
           execute task...

  3. The worker runs up to MAX_CONCURRENT_TASKS tasks at the same time using
     asyncio.Semaphore (like a traffic light that lets N cars through at once).

  4. As the task progresses, the worker updates the DB AND sends WebSocket
     notifications so clients see live progress.

WHY asyncio instead of threading/multiprocessing?
  asyncio is great for I/O-bound work (DB queries, HTTP calls, file I/O).
  For CPU-heavy work (ML inference, image compression), you'd use
  concurrent.futures.ProcessPoolExecutor — that's a natural next step.

WHAT ARE THE SIMULATED EXECUTORS?
  Real-world tasks need domain libraries (boto3, PIL, smtplib, etc.).
  We simulate them with asyncio.sleep + progress updates so you can see
  the PATTERN without needing those libraries installed.
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db.core import async_session_maker
from db.models import Task, TaskStatus, TaskType
from core.websocket_manager import ConnectionManager

logger = logging.getLogger(__name__)


# ── Type alias for executor functions ──────────────────────────────────────────
# An executor is an async function that takes (task, session, manager) and runs the work.
ExecutorFn = Callable[[Task, AsyncSession, ConnectionManager], Awaitable[dict]]


# ── Simulated task executors ───────────────────────────────────────────────────
# Each executor simulates a different type of work.
# They all follow the same contract:
#   • Accept (task, db_session, connection_manager)
#   • Update task.progress periodically
#   • Call manager.broadcast_task_update() to push live updates to clients
#   • Return a dict that becomes task.result

async def _execute_generic(
    task: Task,
    session: AsyncSession,
    manager: ConnectionManager,
) -> dict:
    """
    Default executor: simulates a generic 3-second job with 5 progress updates.
    """
    steps = 5
    for i in range(1, steps + 1):
        await asyncio.sleep(0.6)   # Simulate work being done

        # Calculate progress as a percentage
        task.progress = int((i / steps) * 100)
        session.add(task)
        await session.commit()

        # Push the progress update to all WebSocket subscribers
        await manager.broadcast_task_update(str(task.id), {
            "event":    "progress",
            "status":   task.status.value,
            "progress": task.progress,
            "message":  f"Step {i}/{steps} complete",
        })

    return {"message": "Generic task completed successfully", "steps_completed": steps}


async def _execute_data_export(
    task: Task,
    session: AsyncSession,
    manager: ConnectionManager,
) -> dict:
    """
    Simulates exporting data to a CSV file.
    Progress is reported at each phase: query → format → write → compress.
    """
    phases = [
        (20, "Querying database records..."),
        (50, "Formatting data into CSV rows..."),
        (80, "Writing file to disk..."),
        (100, "Compressing output file..."),
    ]
    for progress, message in phases:
        await asyncio.sleep(0.8)
        task.progress = progress
        session.add(task)
        await session.commit()

        await manager.broadcast_task_update(str(task.id), {
            "event": "progress", "status": task.status.value,
            "progress": progress, "message": message,
        })

    return {
        "file_name": f"export_{task.id}.csv",
        "rows_exported": 1_284,
        "file_size_kb": 48,
    }


async def _execute_send_email(
    task: Task,
    session: AsyncSession,
    manager: ConnectionManager,
) -> dict:
    """
    Simulates sending an email (connecting to SMTP, composing, sending).
    """
    phases = [
        (30, "Connecting to email server..."),
        (60, "Composing email body..."),
        (100, "Email delivered successfully!"),
    ]
    for progress, message in phases:
        await asyncio.sleep(0.5)
        task.progress = progress
        session.add(task)
        await session.commit()

        await manager.broadcast_task_update(str(task.id), {
            "event": "progress", "status": task.status.value,
            "progress": progress, "message": message,
        })

    return {"recipient": "user@example.com", "subject": "Your task is complete", "sent": True}


async def _execute_image_process(
    task: Task,
    session: AsyncSession,
    manager: ConnectionManager,
) -> dict:
    """
    Simulates processing an image (load, resize, convert, save).
    """
    phases = [
        (25, "Loading original image..."),
        (50, "Resizing to 1920×1080..."),
        (75, "Converting to WebP format..."),
        (100, "Saving processed image..."),
    ]
    for progress, message in phases:
        await asyncio.sleep(0.7)
        task.progress = progress
        session.add(task)
        await session.commit()

        await manager.broadcast_task_update(str(task.id), {
            "event": "progress", "status": task.status.value,
            "progress": progress, "message": message,
        })

    return {"output_file": f"processed_{task.id}.webp", "dimensions": "1920x1080", "size_kb": 120}


async def _execute_report_generate(
    task: Task,
    session: AsyncSession,
    manager: ConnectionManager,
) -> dict:
    """
    Simulates generating a PDF report with multiple sections.
    """
    phases = [
        (10, "Gathering data sources..."),
        (30, "Rendering charts and graphs..."),
        (60, "Assembling report sections..."),
        (85, "Applying formatting and styles..."),
        (100, "Exporting final PDF..."),
    ]
    for progress, message in phases:
        await asyncio.sleep(0.6)
        task.progress = progress
        session.add(task)
        await session.commit()

        await manager.broadcast_task_update(str(task.id), {
            "event": "progress", "status": task.status.value,
            "progress": progress, "message": message,
        })

    return {
        "report_url": f"/reports/{task.id}.pdf",
        "pages": 12,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Executor registry ──────────────────────────────────────────────────────────
# Maps each TaskType to the function that handles it.
# This is the "strategy pattern" — easy to add new task types without touching the worker.
EXECUTOR_REGISTRY: dict[TaskType, ExecutorFn] = {
    TaskType.GENERIC:         _execute_generic,
    TaskType.DATA_EXPORT:     _execute_data_export,
    TaskType.SEND_EMAIL:      _execute_send_email,
    TaskType.IMAGE_PROCESS:   _execute_image_process,
    TaskType.REPORT_GENERATE: _execute_report_generate,
}


# ── TaskWorker class ───────────────────────────────────────────────────────────

class TaskWorker:
    """
    The background processing engine.

    Responsibilities:
      1. Accept task IDs into an asyncio.Queue (enqueue)
      2. Pull tasks off the queue and run their executors (process)
      3. Update task status / result in the DB after execution
      4. Push real-time notifications via the ConnectionManager

    CONCURRENCY MODEL:
      asyncio.Semaphore(max_concurrent) acts like a traffic gate.
      If 5 tasks are already running (semaphore count = 0), new tasks
      wait until a slot opens up (a task finishes and releases the semaphore).
    """

    def __init__(self, connection_manager: ConnectionManager, max_concurrent: int = 5) -> None:
        self.manager = connection_manager
        self.max_concurrent = max_concurrent

        # asyncio.Queue is a thread-safe, async-friendly FIFO queue.
        # We store task IDs (UUIDs as strings) here.
        self._queue: asyncio.Queue[str] = asyncio.Queue()

        # Semaphore limits how many tasks run simultaneously.
        # Think of it as N "slots" — each running task holds one slot.
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)

        # Tracks the asyncio background tasks so we can cancel them on shutdown
        self._running_tasks: set[asyncio.Task] = set()

        # Flag to signal the worker loop to stop (used in graceful shutdown)
        self._shutdown = False

    async def enqueue(self, task_id: str) -> None:
        """
        Add a task ID to the processing queue.
        Called by task_service.create_task() after inserting to the DB.
        """
        await self._queue.put(task_id)
        logger.info(f"Task {task_id} enqueued. Queue size: {self._queue.qsize()}")

    async def start(self) -> None:
        """
        Start the background worker loop.
        Called once at application startup (see main.py lifespan).

        This creates an asyncio Task (not our API Task!) that runs
        the _worker_loop() coroutine concurrently with the web server.
        """
        logger.info(f"TaskWorker starting (max concurrent: {self.max_concurrent})")
        # asyncio.ensure_future schedules the coroutine to run in the background
        asyncio.ensure_future(self._worker_loop())

    async def stop(self) -> None:
        """
        Gracefully shut down the worker.
        Called at application shutdown (see main.py lifespan).
        Waits for running tasks to finish before exiting.
        """
        logger.info("TaskWorker shutting down...")
        self._shutdown = True

        # Wait for all currently running asyncio tasks to complete
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)

        logger.info("TaskWorker stopped.")

    async def _worker_loop(self) -> None:
        """
        The main loop: continuously pull task IDs from the queue and process them.

        Each task is processed inside its own asyncio.Task so multiple tasks
        can run concurrently without blocking each other.
        """
        while not self._shutdown:
            try:
                # Wait up to 1 second for a task — then re-check _shutdown
                # asyncio.wait_for raises TimeoutError if nothing arrives in time
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No task arrived — loop back and check if we should shut down
                continue
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                continue

            # Spawn an asyncio Task for this task ID.
            # This allows the loop to keep pulling new tasks from the queue
            # while previous ones are still running (concurrency!).
            coro = self._process_task(task_id)
            asyncio_task = asyncio.ensure_future(coro)

            # Track it so we can wait for it during shutdown
            self._running_tasks.add(asyncio_task)

            # Remove from tracking set when it finishes (memory cleanup)
            asyncio_task.add_done_callback(self._running_tasks.discard)

    async def _process_task(self, task_id: str) -> None:
        """
        Execute a single task end-to-end:
          1. Acquire semaphore slot (rate limiting)
          2. Load task from DB, mark as RUNNING
          3. Run the appropriate executor
          4. Save result or error to DB, mark as COMPLETED/FAILED
          5. Broadcast final status via WebSocket
        """
        # `async with self._semaphore` waits until a slot is available,
        # then "checks in", runs the block, then "checks out" (releases the slot).
        async with self._semaphore:
            # Open a new DB session for this task's entire lifecycle
            async with async_session_maker() as session:
                # ── Load the task ──────────────────────────────────────────────
                task = await session.get(Task, task_id)

                if task is None:
                    logger.error(f"Task {task_id} not found in DB — skipping")
                    return

                # If the user cancelled the task before we got to it, skip it
                if task.status == TaskStatus.CANCELLED:
                    logger.info(f"Task {task_id} was cancelled — skipping")
                    return

                # ── Mark as RUNNING ────────────────────────────────────────────
                task.status     = TaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc)
                task.progress   = 0
                session.add(task)
                await session.commit()

                await self.manager.broadcast_task_update(task_id, {
                    "event":    "started",
                    "status":   task.status.value,
                    "progress": 0,
                    "message":  f"Task '{task.title}' has started",
                })
                logger.info(f"Task {task_id} started (type={task.task_type})")

                # ── Execute ────────────────────────────────────────────────────
                executor = EXECUTOR_REGISTRY.get(task.task_type, _execute_generic)

                try:
                    result = await executor(task, session, self.manager)

                    # ── Mark as COMPLETED ──────────────────────────────────────
                    task.status       = TaskStatus.COMPLETED
                    task.progress     = 100
                    task.result       = result
                    task.completed_at = datetime.now(timezone.utc)
                    session.add(task)
                    await session.commit()

                    await self.manager.broadcast_task_update(task_id, {
                        "event":    "completed",
                        "status":   TaskStatus.COMPLETED.value,
                        "progress": 100,
                        "result":   result,
                        "message":  f"Task '{task.title}' completed successfully!",
                    })
                    logger.info(f"Task {task_id} completed successfully")

                except Exception as exc:
                    # ── Mark as FAILED ─────────────────────────────────────────
                    error_msg = str(exc)
                    logger.error(f"Task {task_id} failed: {error_msg}\n{traceback.format_exc()}")

                    task.status        = TaskStatus.FAILED
                    task.error_message = error_msg
                    task.completed_at  = datetime.now(timezone.utc)
                    session.add(task)
                    await session.commit()

                    await self.manager.broadcast_task_update(task_id, {
                        "event":         "failed",
                        "status":        TaskStatus.FAILED.value,
                        "progress":      task.progress,
                        "error_message": error_msg,
                        "message":       f"Task '{task.title}' failed: {error_msg}",
                    })

    @property
    def queue_size(self) -> int:
        """How many tasks are waiting to be picked up by the worker."""
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        """How many tasks are currently executing."""
        return len(self._running_tasks)
