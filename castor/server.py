import concurrent.futures
import threading
import importlib
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from .core import Manager, Task, TaskResult


def _worker_entrypoint(manager_path: str, task_id: str):
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
    _run_task(manager, task_id)


def _run_task(manager: Manager, task_id: str):
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
        # Handle cancellable and regular tasks differently ---
        if task.cancellable:
            # For cancellable tasks, we iterate through the generator
            generator = func(*task.args, **task.kwargs)
            final_result = None
            while True:
                # 1. Check for cancellation BEFORE the next step
                if manager.is_cancelled(task.id):
                    # Fail the task with a specific "cancelled" message
                    _fail_task(manager, task, "Task was cancelled by user.", status="cancelling")
                    return

                try:
                    # 2. Run the task until the next `yield`
                    next(generator)
                except StopIteration as e:
                    # 3. The generator is finished. Its return value is in e.value.
                    final_result = e.value
                    break  # Exit the loop

            # 4. If the loop finished, the task completed successfully.
            _succeed_task(manager, task, final_result)
        else:
            # For regular tasks, execute as normal
            result = func(*task.args, **task.kwargs)
            _succeed_task(manager, task, result)

    except Exception:
        # 4. Handle failed execution for non-cancellable tasks or setup errors
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
        id=task.id,
        status="success",
        result=result,
    )
    result_queue.put(result_payload, priority=1)

    # NEW: Handle task repetition
    _handle_repetition(manager, task)


def _fail_task(manager: Manager, task: Task, error: str, status: Literal["failed", "cancelling"] = "failed"): # <--- MODIFIED
    """Updates the task's state on failure and pushes the error."""
    task.status = status # <--- MODIFIED
    task.finished_at = datetime.now(timezone.utc).isoformat()
    task.error = error
    manager._tasks[task.id] = task
    if status == "cancelling": # <--- NEW
        manager.info(id=task.id, task=task.task_name, msg="Cancelled.")
    else:
        manager.error(id=task.id, task=task.task_name, msg=error)

    result_queue = manager._db.queue(f"results::{task.id}", model=TaskResult)
    result_payload = TaskResult(
        id=task.id,
        status="error",
        result=None,
        error=error,
    )
    result_queue.put(result_payload, priority=1)

    # NEW: Handle task repetition even on failure
    _handle_repetition(manager, task)


def _handle_repetition(manager: Manager, task: Task):
    """Checks if a task should be repeated and re-enqueues it if necessary."""
    if not task.execute_every:
        return

    until = task.execute_until
    times = task.execute_times
    every = task.execute_every

    now = datetime.now(timezone.utc)

    # Condition 1: Check 'until'
    if until and now >= datetime.fromisoformat(until):
        return

    # Condition 2: Check 'times'
    if times is not None:
        if times <= 1:
            return
        times -= 1

    # If conditions pass, create and enqueue the next task
    next_execute_at = datetime.fromisoformat(task.enqueued_at) + timedelta(
        seconds=every
    )

    new_task = Task(
        id=task.id,
        task_name=task.task_name,
        mode=task.mode,
        daemon=task.daemon,
        cancellable=task.cancellable,
        status="pending",
        args=task.args,
        kwargs=task.kwargs,
        enqueued_at=now.isoformat(),
        execute_at=next_execute_at.isoformat(),
        execute_every=every,
        execute_until=until,
        execute_times=times,
    )

    manager._tasks[new_task.id] = new_task
    ts = next_execute_at.timestamp()
    manager._scheduled_tasks.put(new_task.id, ts)
    manager.info(
        id=new_task.id, task=task.task_name, msg=f"Re-enqueued for repetition."
    )


class Server:
    def __init__(self, manager: Manager, workers: int, threads: int, manager_path: str):
        self._manager = manager
        self._manager_path = manager_path  # Store the path for process tasks
        self._shutdown_event = threading.Event()
        self._process_executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=workers
        )
        self._thread_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=threads
        )

    def serve(self):
        # Main server loop to handle scheduled tasks
        while not self._shutdown_event.is_set():
            try:
                # 1. Check for any due scheduled tasks
                now_timestamp = datetime.now(timezone.utc).timestamp()

                while next_task := self._manager._scheduled_tasks.peek():
                    if next_task.priority <= now_timestamp:
                        self._dispatch_task(next_task.data)
                        self._manager._scheduled_tasks.get()
                    else:
                        break

                # 2. Check for pending tasks (blocking with timeout)
                pending_task_item = self._manager._pending_tasks.get(timeout=1.0)
                if pending_task_item:
                    task_id = pending_task_item.data
                    self._dispatch_task(task_id)

            except TimeoutError:
                # This is expected when the queue is empty, so we just continue
                continue
            except KeyboardInterrupt:
                break

        if not self._shutdown_event.is_set():
            self.stop()

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
            self._process_executor.submit(
                _worker_entrypoint, self._manager_path, task.id
            )
        else:
            self._thread_executor.submit(
                _run_task, self._manager, task.id
            )
