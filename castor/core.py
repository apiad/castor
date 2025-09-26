# castor/core.py

import asyncio
import uuid
import time
from datetime import datetime, timezone, timedelta
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
    # Scheduling and repetition fields
    execute_at: Optional[str] = None  # ISO 8601 timestamp for scheduled tasks
    execute_every: Optional[float] = None  # Interval in seconds
    execute_times: Optional[int] = None  # Number of times to repeat
    execute_until: Optional[str] = None  # ISO 8601 timestamp


class TaskResult(Model):
    id: str
    status: TaskStatus
    error: Optional[str] = None
    result: Any = None


LogLevel = Literal["info", "error"]


class LogMessage(Model):
    id: str | None = None
    task: str | None = None
    message: str
    level: LogLevel


class TaskSchedule(Model):
    id: str
    timestamp: float


class TaskHandle:
    """
    A user-facing handle to an enqueued task, providing methods to check
    its status and retrieve its result.
    """

    def __init__(self, task_id: str, manager: "Manager"):
        self._id = task_id
        self._manager = manager
        # Each task gets a dedicated queue for its result, identified by its ID.
        self._result_queue = self._manager._db.queue(
            f"results::{self._id}", model=TaskResult
        )

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
                raise Exception(
                    result_payload.error
                    or "Task failed without a specific error message."
                )

            return result_payload.result
        except TimeoutError:
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for task '{self.id}' to complete."
            )

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
            raise Exception(
                result_payload.error or "Task failed without a specific error message."
            )

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


class TaskWrapper:
    def __init__(self, manager: "Manager", task_name: str, mode: TaskMode, daemon: bool, callable):
        self.callable = callable
        self.task_name = task_name
        self.mode = mode
        self.daemon = daemon
        self.manager = manager

    def __call__(self, *args, **kwargs):
        return self.callable(*args, **kwargs)

    def submit(self,
        *args,
        at: datetime | None = None,
        delay: timedelta | int | None = None,
        every: timedelta | int | None = None,
        times: int | None = None,
        until: datetime | None = None,
        **kwargs,
    ) -> TaskHandle:
        if at and delay:
            raise ValueError("Cannot specify both 'at' and 'delay'.")
        if (times or until) and not every:
            raise ValueError("'times' and 'until' require 'every' to be set.")

        task_id = str(uuid.uuid4())
        enqueued_time = datetime.now(timezone.utc)
        execute_at_time = enqueued_time

        if at:
            execute_at_time = at
        elif delay:
            delay_seconds = (
                delay.total_seconds() if isinstance(delay, timedelta) else delay
            )
            execute_at_time = enqueued_time + timedelta(seconds=delay_seconds)

        task = Task(
            id=task_id,
            task_name=self.task_name,
            mode=self.mode,
            daemon=self.daemon,
            status="pending",
            args=list(args),
            kwargs=kwargs,
            enqueued_at=enqueued_time.isoformat(),
            execute_at=execute_at_time.isoformat(),
        )

        if every:
            every_seconds = float(
                every.total_seconds() if isinstance(every, timedelta) else every
            )
            task.execute_every=every_seconds
            task.execute_times=times
            task.execute_until=until.isoformat() if until else None

        return self.manager.submit(task, execute_at_time)


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
        # NEW: A sorted set for scheduled tasks. Score is the timestamp.
        self._scheduled_tasks = self._db.queue("castor_scheduled_tasks", model=TaskSchedule)
        # A registry to hold references to the decorated functions.
        self._registry: Dict[str, Callable] = {}
        self._logs = self._db.channel("castor_logs", model=LogMessage)

    def submit(self, task:Task, at: datetime):
        self._tasks[task.id] = task

        ts = at.timestamp()
        # If the task is scheduled for the future, add it to the scheduled set.
        # Otherwise, put it directly into the pending queue.
        if at > datetime.now(timezone.utc):
            self._scheduled_tasks.put(TaskSchedule(id=task.id, timestamp=ts), ts)
        else:
            self._pending_tasks.put(task.id, priority=0)

        return TaskHandle(task.id, self)

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

            return TaskWrapper(self, task_name, mode, daemon, func)

        return decorator

    def info(self, msg: str, id: str | None = None, task: str | None = None):
        self._logs.publish(
            LogMessage(
                id=id,
                task=task,
                message=msg,
                level="info",
            )
        )

    def error(self, msg: str, id: str | None = None, task: str | None = None):
        self._logs.publish(
            LogMessage(
                id=id,
                task=task,
                message=msg,
                level="error",
            )
        )
