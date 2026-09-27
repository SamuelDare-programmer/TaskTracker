"""
main.py — Application Entry Point
─────────────────────────────────────────────────────────────────────────────
This is the first file that runs when you start the server.
Think of it as the "main reception desk" that:
  1. Configures the FastAPI application
  2. Registers all routes (REST + WebSocket)
  3. Handles startup and shutdown tasks via the lifespan context manager
  4. Adds middleware (CORS, logging, etc.)
  5. Runs the development server via uvicorn

STARTUP SEQUENCE:
  uvicorn starts → lifespan() runs startup code → server accepts requests
  CTRL+C pressed → lifespan() runs shutdown code → server stops cleanly

KEY CONCEPT — Lifespan:
  FastAPI's `lifespan` parameter replaces the old @app.on_event("startup")
  decorator. It uses an async context manager (`async with ...`) to clearly
  separate startup code (before `yield`) from shutdown code (after `yield`).
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings

# ── Logging setup ──────────────────────────────────────────────────────────────
# Configure Python's built-in logging BEFORE importing anything that logs.
# This ensures all log messages from every module are formatted consistently.
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Lifespan context manager ───────────────────────────────────────────────────
# Everything BEFORE `yield` runs at startup.
# Everything AFTER `yield` runs at shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.

    STARTUP:
      1. Create database tables (if they don't exist yet)
      2. Start the background TaskWorker

    SHUTDOWN:
      3. Stop the TaskWorker gracefully (drain the queue)
    """
    # ── STARTUP ────────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  Async Task Management API — Starting Up")
    logger.info("=" * 60)
    logger.info(f"  Environment : {'Development' if settings.DEBUG else 'Production'}")
    logger.info(f"  Database    : {settings.DATABASE_URL.split('@')[-1]}")  # hide credentials
    logger.info(f"  Max Tasks   : {settings.MAX_CONCURRENT_TASKS} concurrent")

    # Import here (not at top level) to avoid circular import issues.
    # db.init_db() must be called AFTER all models are imported so that
    # SQLAlchemy's Base.metadata knows about all tables.
    from db.core import init_db
    from core import task_worker

    logger.info("Initializing database tables...")
    await init_db()
    logger.info("Database tables ready.")

    logger.info("Starting background task worker...")
    await task_worker.start()
    logger.info("Task worker is running.")

    logger.info("=" * 60)
    logger.info(f"  API Docs: http://{settings.HOST}:{settings.PORT}/docs")
    logger.info(f"  ReDoc   : http://{settings.HOST}:{settings.PORT}/redoc")
    logger.info("=" * 60)

    yield   # ← Server is now running and accepting requests

    # ── SHUTDOWN ───────────────────────────────────────────────────────────────
    logger.info("Shutting down gracefully...")
    await task_worker.stop()
    logger.info("Task worker stopped. Goodbye!")


# ── Create the FastAPI app ─────────────────────────────────────────────────────
# All these metadata fields appear in the Swagger UI at /docs and /redoc
app = FastAPI(
    title="Async Task Management API",
    description="""
## Overview
A production-ready API for creating and managing background tasks with real-time notifications.

## Key Features
- 🚀 **Create background tasks** that run without blocking the API
- 📡 **Real-time updates** via WebSocket — watch task progress live
- 📊 **Priority queuing** — high-priority tasks run first
- 🔁 **Multiple task types** — generic, data export, email, image processing, report generation
- 📈 **Dashboard stats** — see counts by status at a glance

## WebSocket Usage
Connect to `ws://localhost:8000/ws/tasks/{task_id}` to receive live JSON messages:
```json
{
  "event": "progress",
  "task_id": "uuid",
  "status": "running",
  "progress": 45,
  "message": "Processing step 3/5...",
  "timestamp": "2024-01-01T12:00:00Z"
}
```
    """,
    version="1.0.0",
    contact={"name": "Task API"},
    lifespan=lifespan,   # register our startup/shutdown manager
)


# ── CORS Middleware ────────────────────────────────────────────────────────────
# CORS (Cross-Origin Resource Sharing) controls which domains can call the API.
# During development, allow_origins=["*"] lets any frontend connect.
# In production, replace "*" with your specific frontend domain(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # ← RESTRICT THIS in production!
    allow_credentials=True,
    allow_methods=["*"],           # allow GET, POST, PUT, DELETE, PATCH, etc.
    allow_headers=["*"],           # allow any HTTP headers
)


# ── Register routes ────────────────────────────────────────────────────────────
# We mount each router at a specific prefix.
# All routes in routes/tasks.py get the /tasks prefix (already set in the router).
# All routes in routes/websocket.py get the /ws prefix here.

from routes.tasks     import router as tasks_router
from routes.websocket import router as websocket_router

app.include_router(tasks_router)                  # REST endpoints under /tasks
app.include_router(websocket_router, prefix="/ws")  # WebSocket endpoints under /ws


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"], summary="Health check")
async def health():
    """
    Simple liveness check — if this returns 200, the server is running.
    Load balancers and monitoring tools ping this endpoint.
    """
    from core import task_worker, connection_manager
    return {
        "status": "ok",
        "message": "Async Task Management API is running",
        "worker": {
            "queue_size":   task_worker.queue_size,
            "running_tasks": task_worker.running_count,
        },
        "websockets": {
            "task_subscriptions":   connection_manager.active_task_subscriptions,
            "global_subscriptions": connection_manager.active_global_subscriptions,
        },
    }


# ── Dev server entry point ─────────────────────────────────────────────────────
# This block runs when you execute `python main.py` directly.
# In production, you'd use: uvicorn main:app --host 0.0.0.0 --port 8000
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,   # auto-restart when code changes (dev only)
        log_level="debug" if settings.DEBUG else "info",
    )