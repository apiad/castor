import concurrent.futures
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Literal

from beaver import Model
from .core import Manager, Task, TaskResult


LogLevel = Literal["info", "error"]


class LogMessage(Model):
    id: str | None = None
    task: str | None = None
    message: str
    level: LogLevel


class Server:
    """
    A worker server that fetches tasks from the pending queue, executes them
    concurrently, and reports their results.
    """

    def __init__(self, manager: Manager, workers: int = 4, threads: int = 8):
        """
        Initializes the server with a task manager and executor pools.

        Args:
            manager: The Castor Manager instance that knows about the tasks.
            workers: The number of processes for the CPU-bound task pool.
            threads: The number of threads for the I/O-bound task pool.
        """
        self._manager = manager
        self._shutdown_event = threading.Event()
        self._process_executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=workers
        )
        self._thread_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=threads
        )
        self._log_channel = manager._db.channel("castor_logs", model=LogMessage)

    def serve(self):
        """
        Starts the main worker loop, continuously fetching and dispatching tasks.
        This loop will run until a shutdown is initiated (e.g., via Ctrl+C).
        """
        print("Starting worker server...")
        try:
            while not self._shutdown_event.is_set():
                try:
                    # Block and wait for a task, with a timeout to allow periodic
                    # checks of the shutdown event.
                    pending_task_item = self._manager._pending_tasks.get(timeout=1.0)
                    task_id = pending_task_item.data
                    self._dispatch_task(task_id)
                except TimeoutError:
                    # No task in the queue, loop again to check for shutdown.
                    continue
        except KeyboardInterrupt:
            print("\nShutdown signal received.")
        finally:
            self.stop()

    def info(self, msg: str, id: str | None = None, task: str | None = None):
        self._log_channel.publish(
            LogMessage(
                id=id,
                task=task,
                message=msg,
                level="info",
            )
        )

    def error(self, msg: str, id: str | None = None, task: str | None = None):
        self._log_channel.publish(
            LogMessage(
                id=id,
                task=task,
                message=msg,
                level="error",
            )
        )

    def stop(self):
        """Initiates a graceful shutdown of the worker server and its executors."""
        self.info("Shutting down executors...")
        self._shutdown_event.set()
        # shutdown(wait=True) will wait for all non-daemon tasks to complete.
        self._thread_executor.shutdown(wait=True)
        self._process_executor.shutdown(wait=True)
        self.info("Server stopped.")

    def _dispatch_task(self, task_id: str):
        """
        Validates a task and submits it to the appropriate executor pool.
        """
        task = self._manager.get_task(task_id)
        if not task:
            self.error(id=task_id, msg="Not found in database, skipping.")
            return

        if task.task_name not in self._manager._registry:
            error_msg = f"Task function '{task.task_name}' is not registered."
            self.error(id=task_id, task=task.task_name, msg="Not registered.")
            self._fail_task(task, error_msg)
            return

        # Choose the executor based on the task's registered mode.
        executor = (
            self._process_executor if task.mode == "process" else self._thread_executor
        )
        executor.submit(self._run_task_wrapper, task.id)

    def _run_task_wrapper(self, task_id: str):
        """
        The actual function executed by the pool. It manages the full lifecycle
        of a single task execution, from updating its status to reporting the result.
        """
        task = self._manager.get_task(task_id)
        if not task:
            return

        # 1. Update status to 'running'
        task.status = "running"
        task.started_at = datetime.now(timezone.utc).isoformat()
        self._manager._tasks[task.id] = task

        func = self._manager.get_callable(task.task_name)

        try:
            # 2. Execute the actual task function
            self.info(id=task.id, task=task.task_name, msg="Starting.")
            result = func(*task.args, **task.kwargs)
            # 3. Handle successful execution
            self._succeed_task(task, result)
        except Exception:
            # 4. Handle failed execution
            error_message = traceback.format_exc()
            self._fail_task(task, error_message)

    def _succeed_task(self, task: Task, result: Any):
        """Updates the task's state on success and pushes the result."""
        task.status = "success"
        task.finished_at = datetime.now(timezone.utc).isoformat()
        task.result = result
        self._manager._tasks[task.id] = task

        # Push the result to the task's dedicated result queue.
        result_queue = self._manager._db.queue(f"results::{task.id}", model=TaskResult)
        result_payload = TaskResult(
            id=task.id,
            status="success",
            result=result,
        )
        result_queue.put(result_payload, priority=1)
        self.info(id=task.id, task=task.task_name, msg="Completed successfully.")

    def _fail_task(self, task: Task, error: str):
        """Updates the task's state on failure and pushes the error."""
        task.status = "failed"
        task.finished_at = datetime.now(timezone.utc).isoformat()
        task.error = error
        self._manager._tasks[task.id] = task

        # Push the error to the task's dedicated result queue.
        result_queue = self._manager._db.queue(f"results::{task.id}", model=TaskResult)
        result_payload = TaskResult(
            id=task.id,
            status="error",
            result=None,
            error=error,
        )
        result_queue.put(result_payload, priority=1)
        self.error(id=task.id, task=task.task_name, msg=error)
