import types
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Literal, Optional

from beaver import BeaverDB, Model

# --- Public API Models & Types ---

TaskStatus = Literal["pending", "running", "success", "failed", "cancelling"]
TaskMode = Literal["thread", "process"]


class Task(Model):
    """Data model for a task's state, stored in the database."""

    id: str
    task_name: str
    status: TaskStatus
    mode: TaskMode
    daemon: bool
    cancellable: bool  # <--- NEW
    args: List[Any]
    kwargs: Dict[str, Any]
    enqueued_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    execute_at: Optional[str] = None
    execute_every: Optional[float] = None
    execute_times: Optional[int] = None
    execute_until: Optional[str] = None


class TaskResult(Model):
    """Data model for a task's result, sent via a dedicated queue."""

    id: str
    status: TaskStatus
    error: Optional[str] = None
    result: Any = None


LogLevel = Literal["info", "error"]


class LogMessage(Model):
    """Data model for log messages."""

    id: str | None = None
    task: str | None = None
    message: str
    level: LogLevel


class TaskHandle:
    """A user-facing handle to an enqueued task."""

    def __init__(self, task_id: str, manager: "Manager"):
        self._id = task_id
        self._manager = manager
        self._result_queue = self._manager._db.queue(
            f"results::{self._id}", model=TaskResult
        )

    @property
    def id(self) -> str:
        return self._id

    def status(self) -> TaskStatus:
        task_doc = self._manager.get_task(self._id)
        if task_doc is None:
            raise ValueError(f"Task with ID '{self._id}' not found.")
        return task_doc.status

    def cancel(self):
        """Requests the task to be cancelled by setting a flag with a TTL."""
        self._manager._cancellation_flags.set(self.id, True)

    def join(self, timeout: Optional[float] = None) -> Any:
        try:
            item = self._result_queue.get(timeout=timeout)
            result_payload = item.data
            if result_payload.status == "failed":
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
        async_result_queue = self._result_queue.as_async()
        item = await async_result_queue.get(timeout=timeout)
        result_payload = item.data
        if result_payload.status == "failed":
            raise Exception(
                result_payload.error or "Task failed without a specific error message."
            )
        return result_payload.result

    def __bool__(self) -> bool:
        current_status = self.status()
        return current_status in ["success", "failed"]

    def __repr__(self) -> str:
        try:
            status = self.status()
        except Exception:
            status = "unknown"
        return f"<TaskHandle(id='{self.id}', status='{status}')>"


# --- Internal Classes for the Proxy Pattern ---


class _BoundTask:
    """Internal object that represents a task function bound to a specific manager."""

    def __init__(
        self,
        manager: "Manager",
        task_name: str,
        mode: TaskMode,
        daemon: bool,
        cancellable: bool,
        callable,
    ):
        self.callable = callable
        self.task_name = task_name
        self.mode = mode
        self.daemon = daemon
        self.cancellable = cancellable  # <--- NEW
        self.manager = manager

    def __call__(self, *args, **kwargs):
        return self.callable(*args, **kwargs)

    def submit(self, *args, **kwargs) -> TaskHandle:
        # This is just a pass-through to the manager's internal submit logic.
        return self.manager._create_and_submit_task(self, *args, **kwargs)


class TaskProxy:
    """A lightweight, unbound proxy for a task function."""

    def __init__(
        self, func: Callable, mode: TaskMode, daemon: bool, cancellable: bool
    ):  # <--- MODIFIED
        self.callable = func
        self.mode = mode
        self.daemon = daemon
        self.cancellable = cancellable  # <--- NEW
        self.task_name = func.__name__
        self._bound_task: Optional[_BoundTask] = None

    def _bind(self, manager: "Manager"):
        """Binds this proxy to a manager, making it live."""
        self._bound_task = _BoundTask(
            manager=manager,
            task_name=self.task_name,
            mode=self.mode,
            daemon=self.daemon,
            cancellable=self.cancellable,  # <--- NEW
            callable=self.callable,
        )
        # Add the raw function to the manager's registry for the worker to find.
        manager._registry[self.task_name] = self.callable

    def submit(
        self,
        *args,
        at: datetime | None = None,
        delay: timedelta | int | None = None,
        every: timedelta | int | None = None,
        times: int | None = None,
        until: datetime | None = None,
        **kwargs,
    ) -> TaskHandle:
        """Submits the task for execution via the bound manager."""
        if self._bound_task is None:
            raise RuntimeError(
                f"Task '{self.task_name}' has not been bound to a Manager instance. "
                "Did you forget to pass the task module to the Manager constructor "
                "or use the @manager.task decorator?"
            )
        return self._bound_task.submit(
            *args, at=at, delay=delay, every=every, times=times, until=until, **kwargs
        )


# --- Public Decorator for "Decoupled Mode" ---


def task(
    mode: TaskMode, daemon: bool = False, cancellable: bool = False
) -> Callable[[Callable], TaskProxy]:
    """
    Decorator to define a task for 'Decoupled Mode'.
    This creates a lightweight, unbound proxy that must be bound to a Manager
    at application startup.
    """

    def decorator(func: Callable) -> TaskProxy:
        return TaskProxy(func, mode, daemon, cancellable)

    return decorator


# --- The Central Manager Class ---


class Manager:
    """
    The central object for managing, binding, and dispatching tasks.
    """

    def __init__(self, db: BeaverDB, tasks: Optional[List[Any]] = None):
        self._db = db
        self._tasks = self._db.dict("castor_tasks", model=Task)
        self._pending_tasks = self._db.queue("castor_pending_tasks")
        self._scheduled_tasks = self._db.queue("castor_scheduled_tasks")
        self._cancellation_flags = self._db.dict("castor_cancellation_flags")
        self._registry: Dict[str, Callable] = {}
        self._logs = self._db.channel("castor_logs", model=LogMessage)

        if tasks:
            self.bind(tasks)

    def bind(self, tasks_or_modules: List[Any]):
        """
        Scans modules or lists for TaskProxy objects and binds them to this
        manager instance, making them ready for submission.
        """
        for item in tasks_or_modules:
            if isinstance(item, types.ModuleType):
                for attr in (getattr(item, name) for name in dir(item)):
                    if isinstance(attr, TaskProxy):
                        attr._bind(self)
            elif isinstance(item, TaskProxy):
                item._bind(self)

    def task(
        self, mode: TaskMode, daemon: bool = False, cancellable: bool = False
    ) -> Callable[[Callable], TaskProxy]:  # <--- MODIFIED
        """
        Decorator for 'Simple Mode'. Creates a task and immediately binds it
        to this manager instance. Ideal for single-file scripts.
        """

        def decorator(func: Callable) -> TaskProxy:
            proxy = task(mode=mode, daemon=daemon, cancellable=cancellable)(
                func
            )  # <--- MODIFIED
            self.bind([proxy])
            return proxy

        return decorator

    def _create_and_submit_task(
        self,
        bound_task: _BoundTask,
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

        task_payload = Task(
            id=task_id,
            task_name=bound_task.task_name,
            mode=bound_task.mode,
            daemon=bound_task.daemon,
            cancellable=bound_task.cancellable,
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
            task_payload.execute_every = every_seconds
            task_payload.execute_times = times
            task_payload.execute_until = until.isoformat() if until else None

        return self.submit(task_payload, execute_at_time)

    def submit(self, task: Task, at: datetime):
        self._tasks[task.id] = task
        ts = at.timestamp()
        if at > datetime.now(timezone.utc):
            self._scheduled_tasks.put(task.id, ts)
        else:
            self._pending_tasks.put(task.id, priority=0)
        return TaskHandle(task.id, self)

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_callable(self, task_name: str) -> Callable:
        return self._registry[task_name]

    def is_cancelled(self, task_id: str) -> bool:
        """Checks if a cancellation flag has been set for the task."""
        return task_id in self._cancellation_flags

    def info(self, msg: str, id: str | None = None, task: str | None = None):
        self._logs.publish(LogMessage(id=id, task=task, message=msg, level="info"))

    def error(self, msg: str, id: str | None = None, task: str | None = None):
        self._logs.publish(LogMessage(id=id, task=task, message=msg, level="error"))
