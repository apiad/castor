import concurrent.futures
import threading
import time
import importlib
import traceback
from datetime import datetime, timezone
from typing import Any, Literal

from beaver import Model
from .core import Manager, Task, TaskResult, LogMessage


def _process_worker_entrypoint(manager_path: str, task_id: str):
    """
    This is the TOP-LEVEL function that gets executed in the new process.
    It is responsible for re-initializing the manager and running the task.
    """
    # 1. Re-create the manager instance from the import path
    try:
        module_path, variable_name = manager_path.split(":", 1)
        module = importlib.import_module(module_path)
        manager = getattr(module, variable_name)
    except (ImportError, AttributeError, ValueError) as e:
        # If we can't even load the manager, we can't log to the DB.
        # This is a catastrophic failure.
        print(f"[PROCESS WORKER ERROR] Could not initialize manager: {e}")
        return

    # 2. Now that we have a manager, we can use the normal task execution logic
    _run_task_wrapper(manager, task_id)


def _run_task_wrapper(manager: Manager, task_id: str):
    """
    The actual function executed by the pool. Manages the lifecycle of a task.
    This can now be called by both threads and the process entrypoint.
    """
    task = manager.get_task(task_id)
    if not task:
        return

    # 1. Update status to 'running' and log
    task.status = "running"
    task.started_at = datetime.now(timezone.utc).isoformat()
    manager._tasks[task.id] = task
    manager.info(id=task.id, task=task.task_name, msg=f"Starting.")

    func = manager.get_callable(task.task_name)

    try:
        # 2. Execute the actual task function
        result = func(*task.args, **task.kwargs)
        # 3. Handle successful execution
        _succeed_task(manager, task, result)
    except Exception:
        # 4. Handle failed execution
        error_message = traceback.format_exc()
        _fail_task(manager, task, error_message)


def _succeed_task(manager: Manager, task: Task, result: Any):
    """Updates the task's state on success and pushes the result."""
    task.status = "success"
    task.finished_at = datetime.now(timezone.utc).isoformat()
    task.result = result
    manager._tasks[task.id] = task
    manager.info(id=task.id, task=task.task_name, msg="Completed.")

    result_queue = manager._db.queue(f"results::{task.id}", model=TaskResult)
    result_payload = TaskResult(
        id=task.id, status="success", result=result,
    )
    result_queue.put(result_payload, priority=1)


def _fail_task(manager: Manager, task: Task, error: str):
    """Updates the task's state on failure and pushes the error."""
    task.status = "failed"
    task.finished_at = datetime.now(timezone.utc).isoformat()
    task.error = error
    manager._tasks[task.id] = task
    manager.error(id=task.id, task=task.task_name, msg=error)

    result_queue = manager._db.queue(f"results::{task.id}", model=TaskResult)
    result_payload = TaskResult(
        id=task.id, status="error", result=None, error=error,
    )
    result_queue.put(result_payload, priority=1)



class Server:
    def __init__(self, manager: Manager, workers: int, threads: int, manager_path: str):
        self._manager = manager
        self._manager_path = manager_path # Store the path for process tasks
        self._shutdown_event = threading.Event()
        self._process_executor = concurrent.futures.ProcessPoolExecutor(max_workers=workers)
        self._thread_executor = concurrent.futures.ThreadPoolExecutor(max_workers=threads)

    def serve(self):
        while not self._shutdown_event.is_set():
            try:
                pending_task_item = self._manager._pending_tasks.get(timeout=1.0)
                task_id = pending_task_item.data
                self._dispatch_task(task_id)
            except TimeoutError:
                continue
            except KeyboardInterrupt:
                break

    def stop(self):
        self._shutdown_event.set()
        self._thread_executor.shutdown(wait=True)
        self._process_executor.shutdown(wait=True)

    def _dispatch_task(self, task_id: str):
        task = self._manager.get_task(task_id)
        if not task:
            self._manager.error(id=task_id, msg=f"Task not found.")
            return

        if task.task_name not in self._manager._registry:
            error_msg = f"Task function '{task.task_name}' is not registered."
            self._manager.error(id=task_id, task=task.task_name, msg="Not registered")
            _fail_task(self._manager, task, error_msg)
            return

        if task.mode == "process":
            # Submit the top-level entrypoint with serializable args
            self._process_executor.submit(
                _process_worker_entrypoint, self._manager_path, task.id
            )
        else: # mode == "thread"
            # Threads can access the manager instance directly
            self._thread_executor.submit(_run_task_wrapper, self._manager, task.id)
