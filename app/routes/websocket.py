"""
routes/websocket.py — WebSocket Endpoints for Real-Time Notifications
─────────────────────────────────────────────────────────────────────────────
WebSocket endpoints are different from regular HTTP endpoints:
  - The connection stays OPEN after the initial handshake
  - The server can push data to the client at any time
  - The connection closes when either side disconnects

HOW TO CONNECT (from JavaScript):
  // Watch a specific task
  const ws = new WebSocket("ws://localhost:8000/ws/tasks/your-task-uuid-here")
  ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      console.log(data.event, data.progress, data.message)
  }

  // Watch all tasks (admin/dashboard)
  const ws = new WebSocket("ws://localhost:8000/ws/tasks")

MESSAGE FORMAT (JSON):
  {
    "event":    "progress" | "started" | "completed" | "failed" | "connected",
    "task_id":  "uuid-string",
    "status":   "pending" | "running" | "completed" | "failed" | "cancelled",
    "progress": 0-100,
    "message":  "Human-readable description",
    "result":   {...},        // only on "completed"
    "error_message": "...",   // only on "failed"
    "timestamp": "2024-01-01T00:00:00Z"
  }
─────────────────────────────────────────────────────────────────────────────
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.core import async_session_maker
from core import connection_manager   # The singleton ConnectionManager

logger = logging.getLogger(__name__)

# No prefix here — we mount this router at /ws in main.py
router = APIRouter(tags=["WebSockets"])


# ── WS /ws/tasks/{task_id} ─────────────────────────────────────────────────────
@router.websocket("/tasks/{task_id}")
async def websocket_task(task_id: str, websocket: WebSocket):
    """
    Subscribe to real-time updates for a specific task.

    The client connects and receives a stream of JSON messages
    as the task moves through its lifecycle (pending → running → completed).

    HOW THE LOOP WORKS:
      1. We accept the connection and register it with the ConnectionManager.
      2. We enter a loop: wait for any message from the client.
         (We don't expect clients to send us anything, but we need to keep
          the loop running to detect disconnects.)
      3. When the client disconnects (WebSocketDisconnect is raised),
         we clean up by unregistering the connection.

    WHY receive_text() IN THE LOOP?
      FastAPI WebSockets need you to actively receive messages to detect
      when the client disconnects. Without this loop, the server would
      never know the client left, leaking memory.
    """
    # Validate that the task exists before accepting the WebSocket
    async with async_session_maker() as db:
        from db.models import Task
        task = await db.get(Task, task_id)
        if task is None:
            # Reject the WebSocket with a 1008 close code (policy violation)
            await websocket.close(code=1008, reason=f"Task '{task_id}' not found")
            return

    # Register the connection — the worker will now push updates here
    await connection_manager.connect_to_task(task_id, websocket)
    logger.info(f"Client subscribed to task {task_id}")

    try:
        # Keep the connection alive by waiting for client messages.
        # Clients won't usually send anything — we're just keeping the socket open.
        while True:
            # This will block until the client sends a message OR disconnects.
            # receive_text() raises WebSocketDisconnect when the client closes.
            message = await websocket.receive_text()
            # Optionally: handle client messages here (e.g., "cancel" command)
            logger.debug(f"Received message from client on task {task_id}: {message}")

    except WebSocketDisconnect:
        # Client disconnected — clean up the registration
        connection_manager.disconnect_from_task(task_id, websocket)
        logger.info(f"Client unsubscribed from task {task_id}")

    except Exception as e:
        # Unexpected error — still clean up
        connection_manager.disconnect_from_task(task_id, websocket)
        logger.error(f"WebSocket error for task {task_id}: {e}")


# ── WS /ws/tasks ───────────────────────────────────────────────────────────────
@router.websocket("/tasks")
async def websocket_all_tasks(websocket: WebSocket):
    """
    Subscribe to real-time updates for ALL tasks.

    Useful for:
      - Admin dashboards that need a live feed of all activity
      - Monitoring tools
      - Notification center that shows updates for all user's tasks

    This stream is higher volume — the client receives every event
    from every task on the system.
    """
    await connection_manager.connect_global(websocket)
    logger.info("Client subscribed to global task feed")

    try:
        while True:
            message = await websocket.receive_text()
            logger.debug(f"Global feed received client message: {message}")

    except WebSocketDisconnect:
        connection_manager.disconnect_global(websocket)
        logger.info("Client unsubscribed from global task feed")

    except Exception as e:
        connection_manager.disconnect_global(websocket)
        logger.error(f"Global WebSocket error: {e}")
