"""
core/websocket_manager.py — Real-Time Notification Hub
─────────────────────────────────────────────────────────────────────────────
This module manages ALL active WebSocket connections.

WHAT IS A WEBSOCKET?
  Unlike regular HTTP (client asks → server answers → connection closes),
  WebSockets keep the connection OPEN so the server can push data to the
  client at any time. Perfect for live updates like task progress.

  Client  ←────────────── Server
            "task is 50% done"
            "task is 100% done!"

DESIGN: Two subscription types
  1. Task-specific: client connects to /ws/tasks/{task_id}
     → only receives updates for that ONE task
  2. Global: client connects to /ws/tasks
     → receives updates for ALL tasks (admin / dashboard view)

DATA STRUCTURE:
  We use a dict to map task IDs to a list of active WebSocket connections.
  Multiple clients can watch the same task (e.g., a user + an admin).

  task_connections: {
      "task-uuid-1": [<WebSocket A>, <WebSocket B>],
      "task-uuid-2": [<WebSocket C>],
  }

  global_connections: [<WebSocket D>, <WebSocket E>]
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any
from uuid import UUID

from fastapi import WebSocket

# Module-level logger — each module gets its own named logger.
# This makes it easy to filter logs by module in production.
logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections and broadcasts messages to subscribers.

    Think of this class as a telephone switchboard:
      - Clients "plug in" when they connect.
      - We "ring" the right clients when a task has news.
      - Clients "unplug" when they disconnect.
    """

    def __init__(self) -> None:
        # Maps task_id (str) → list of WebSocket objects watching that task
        self.task_connections: Dict[str, List[WebSocket]] = {}

        # List of WebSocket connections subscribed to ALL task events
        self.global_connections: List[WebSocket] = []

    # ── Connection lifecycle ───────────────────────────────────────────────────

    async def connect_to_task(self, task_id: str, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection and register it for a specific task.

        IMPORTANT: websocket.accept() MUST be called before sending/receiving.
        It completes the HTTP → WebSocket upgrade handshake.
        """
        await websocket.accept()

        # If this is the first subscriber for this task, create the list
        if task_id not in self.task_connections:
            self.task_connections[task_id] = []

        self.task_connections[task_id].append(websocket)
        logger.info(f"WebSocket connected for task {task_id}. "
                    f"Total subscribers: {len(self.task_connections[task_id])}")

        # Send a welcome message so the client knows the connection is live
        await self._send_json(websocket, {
            "event": "connected",
            "task_id": task_id,
            "message": f"Subscribed to task {task_id}",
            "timestamp": _utc_now(),
        })

    async def connect_global(self, websocket: WebSocket) -> None:
        """
        Accept a WebSocket that wants to receive ALL task events.
        """
        await websocket.accept()
        self.global_connections.append(websocket)
        logger.info(f"Global WebSocket connected. Total global: {len(self.global_connections)}")

        await self._send_json(websocket, {
            "event": "connected",
            "message": "Subscribed to all task events",
            "timestamp": _utc_now(),
        })

    def disconnect_from_task(self, task_id: str, websocket: WebSocket) -> None:
        """
        Remove a WebSocket from the task's subscriber list.
        Called when the client disconnects (browser closed, network drop, etc.)
        """
        if task_id in self.task_connections:
            try:
                self.task_connections[task_id].remove(websocket)
            except ValueError:
                pass  # Already removed — that's fine

            # Clean up the empty list to free memory
            if not self.task_connections[task_id]:
                del self.task_connections[task_id]

            logger.info(f"WebSocket disconnected from task {task_id}")

    def disconnect_global(self, websocket: WebSocket) -> None:
        """Remove a global WebSocket subscriber."""
        try:
            self.global_connections.remove(websocket)
        except ValueError:
            pass
        logger.info("Global WebSocket disconnected")

    # ── Broadcasting ───────────────────────────────────────────────────────────

    async def broadcast_task_update(self, task_id: str, data: dict) -> None:
        """
        Send a JSON message to ALL clients watching a specific task,
        AND to all global subscribers.

        This is the core notification method — the worker calls this
        every time a task's status or progress changes.

        WHY asyncio.gather()?
          We want to send to all clients concurrently, not one-at-a-time.
          asyncio.gather() starts all the send coroutines simultaneously
          and waits for them all to finish. Much faster than a for-loop.
        """
        # Add metadata to the payload
        data["task_id"] = task_id
        data["timestamp"] = _utc_now()

        # Collect all the coroutines we want to run concurrently
        send_coroutines = []

        # 1. Send to task-specific subscribers
        task_subs = self.task_connections.get(task_id, [])
        for ws in list(task_subs):   # list() creates a copy — safe if we modify during iteration
            send_coroutines.append(self._send_json(ws, data))

        # 2. Also send to global subscribers
        for ws in list(self.global_connections):
            send_coroutines.append(self._send_json(ws, data))

        if send_coroutines:
            # return_exceptions=True prevents one failed send from cancelling the others
            await asyncio.gather(*send_coroutines, return_exceptions=True)

    async def broadcast_all(self, data: dict) -> None:
        """
        Send a JSON message to EVERY connected client (task-specific + global).
        Useful for system-wide announcements.
        """
        data["timestamp"] = _utc_now()
        all_websockets = (
            [ws for subs in self.task_connections.values() for ws in subs]
            + self.global_connections
        )
        await asyncio.gather(
            *[self._send_json(ws, data) for ws in all_websockets],
            return_exceptions=True,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _send_json(websocket: WebSocket, data: dict) -> None:
        """
        Safely send a JSON payload to a single WebSocket client.

        WHY the try/except?
          If the client disconnected at exactly the wrong moment, `send_json`
          raises an exception. We catch it and log it instead of crashing the
          whole server — a "fire and forget" pattern.
        """
        try:
            await websocket.send_json(data)
        except Exception as e:
            # This is normal — clients disconnect all the time
            logger.debug(f"Failed to send WebSocket message (client may have disconnected): {e}")

    @property
    def active_task_subscriptions(self) -> int:
        """Total number of task-specific WebSocket connections."""
        return sum(len(v) for v in self.task_connections.values())

    @property
    def active_global_subscriptions(self) -> int:
        """Total number of global WebSocket connections."""
        return len(self.global_connections)


# ── Utility ────────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (WebSocket-friendly)."""
    return datetime.now(timezone.utc).isoformat()
