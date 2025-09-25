import asyncio
import uuid
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from beaver import BeaverDB, Model

# Define the possible states a task can be in for type safety.
TaskStatus = Literal["pending", "running", "success", "failed"]
TaskMode = Literal["thread", "process"]

class Task(Model):
    """
    The data model for a task, inheriting from beaver.Model for automatic
    serialization. This represents the state of a task stored in the database.
    """
    id: str
    task_name: str
    status: TaskStatus
    mode: TaskMode
    daemon: bool
    args: List[Any]
    kwargs: Dict[str, Any]
    enqueued_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Any = None
    error: Optional[str] = None


class TaskResult(Model):
    id: str
    status: TaskStatus
    error: Optional[str] = None
    result: Any = None


class TaskHandle:
    """
    A user-facing handle to an enqueued task, providing methods to check
    its status and retrieve its result.
    """
    def __init__(self, task_id: str, manager: "Manager"):
        self._id = task_id
        self._manager = manager
        # Each task gets a dedicated queue for its result, identified by its ID.
        self._result_queue = self._manager._db.queue(f"results::{self._id}", model=TaskResult)

    @property
    def id(self) -> str:
        """The unique ID of the task."""
        return self._id

    def status(self) -> TaskStatus:
        """
        Retrieves the current status of the task from the database.

        Returns:
            The current status as a string: "pending", "running", "success", or "failed".
        """
        task_doc = self._manager.get_task(self._id)
        if task_doc is None:
            raise ValueError(f"Task with ID '{self._id}' not found.")
        return task_doc.status

    def join(self, timeout: Optional[float] = None) -> Any:
        """
        Blocks execution until the task is complete and returns its result.
        This is an efficient operation that waits on a dedicated result queue.

        Args:
            timeout: The maximum number of seconds to wait for the result.
                     If None, it will wait indefinitely.

        Returns:
            The return value of the task.

        Raises:
            TimeoutError: If the timeout is reached before the task completes.
            Exception: If the task failed, this method will re-raise the exception
                       that occurred in the worker.
        """
        try:
            # This is a blocking call that waits for the worker to put the result.
            item = self._result_queue.get(timeout=timeout)
            result_payload = item.data

            if result_payload.status == "failed":
                # Re-raise the exception from the worker.
                raise Exception(result_payload.error or "Task failed without a specific error message.")

            return result_payload.result
        except TimeoutError:
            raise TimeoutError(f"Timed out after {timeout}s waiting for task '{self.id}' to complete.")

    async def resolve(self, timeout: Optional[float] = None) -> Any:
        """
        Asynchronously waits for the task to complete and returns its result.
        (Async functionality to be fully implemented later)
        """
        # This uses the async version of the BeaverDB queue.
        async_result_queue = self._result_queue.as_async()
        item = await async_result_queue.get(timeout=timeout)
        result_payload = item.data

        if result_payload.status == "failed":
            raise Exception(result_payload.error or  "Task failed without a specific error message.")

        return result_payload.result

    def __bool__(self) -> bool:
        """
        Checks if the task is finished (either successfully or failed).

        Returns:
            True if the task is complete, False otherwise.
        """
        current_status = self.status()
        return current_status in ["success", "failed"]

    def __repr__(self) -> str:
        # Prevent circular calls by not calling status() in repr if manager isn't fully ready
        try:
            status = self.status()
        except Exception:
            status = "unknown"
        return f"<TaskHandle(id='{self.id}', status='{status}')>"


class Manager:
    """
    The central object for managing tasks. It holds the database connection
    and serves as the entry point for defining and dispatching tasks.
    """
    def __init__(self, db: BeaverDB):
        self._db = db
        # A dictionary for the state of every task, keyed by task ID.
        self._tasks = self._db.dict("castor_tasks", model=Task)
        # The central queue where new tasks are placed for workers.
        self._pending_tasks = self._db.queue("castor_pending_tasks")
        # A registry to hold references to the decorated functions.
        self._registry: Dict[str, Callable] = {}

    def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieves a task's state document from the database."""
        # Use the efficient .get() method from beaver's DictManager.
        # It automatically deserializes the JSON data back into a Task object.
        task_doc = self._tasks.get(task_id)
        if task_doc:
            return task_doc
        return None

    def get_callable(self, task_name: str) -> Callable:
        """
        Retrieves the registered task function and its metadata.
        This is used by the worker to find the function to execute.
        """
        return self._registry[task_name]

    def task(self, mode: TaskMode, daemon: bool = False):
        """
        A decorator to register a function as a background task.

        Args:
            mode: The concurrency model ('thread' for I/O-bound, 'process' for CPU-bound).
            daemon: If True, the task is non-critical and can be terminated on worker shutdown.
        """
        def decorator(func: Callable):
            task_name = f"{func.__name__}"

            if task_name in self._registry:
                raise ValueError(f"Task with name '{task_name}' is already registered.")

            # Store task metadata, including mode and daemon status.
            self._registry[task_name] = func

            def delay(*args, **kwargs) -> TaskHandle:
                """
                A non-blocking call that enqueues the task and immediately
                returns a handle to it.
                """
                task_id = str(uuid.uuid4())
                enqueued_time = datetime.now(timezone.utc).isoformat()

                # Create the task state document.
                task_doc = Task(
                    id=task_id,
                    task_name=task_name,
                    mode=mode,
                    daemon=daemon,
                    status="pending",
                    args=list(args),
                    kwargs=kwargs,
                    enqueued_at=enqueued_time,
                )

                # Store the state and push the task to the pending queue.
                # Use dictionary assignment, which is the API for beaver.dict.
                self._tasks[task_id] = task_doc
                self._pending_tasks.put(task_id, priority=0)

                return TaskHandle(task_id, self)

            # Attach the .delay() method to the original function object.
            func.delay = delay # type: ignore
            return func
        return decorator
