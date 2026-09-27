"""
core/__init__.py
Makes `core` a Python package and exposes the singletons that the rest of
the app imports.

SINGLETONS EXPLAINED:
  A singleton is an object that is created ONCE and then reused everywhere.
  `connection_manager` and `task_worker` must be singletons because:
    - ConnectionManager tracks ALL active WebSocket connections in memory.
      Two separate instances would each track different connections — broken!
    - TaskWorker owns the asyncio.Queue. Two instances = two queues — tasks
      would go into one queue but the worker would read from the other.
"""
from core.websocket_manager import ConnectionManager
from core.worker import TaskWorker

# Create the single shared instances here.
# Every other module imports THESE objects (not the classes).
connection_manager = ConnectionManager()
task_worker        = TaskWorker(connection_manager)

__all__ = ["connection_manager", "task_worker"]
