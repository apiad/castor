# Castor

Castor is a minimalistic, embedded task manager built on [BeaverDB](https://github.com/apiad/beaver). It's designed to run background tasks (both process and thread-based) in applications where a complicated, distributed broker like Redis is overkill.

It embraces the minimalistic philosophy of BeaverDB while still being feature-full for the use cases where it makes sense.

## Core Philosophy

- **Explicit Over Implicit:** The library avoids global state. Configuration is handled through explicit `Manager` objects, making applications more robust and testable.
- **Single Responsibility:** `castor` is a task queueing and execution library. Nothing else, nothing more.
- **Developer Experience:** The API is designed to be intuitive, requiring minimal boilerplate to turn a function into a background task.
- **Decoupled Architecture:** The application that enqueues a task is fully separate from the worker process that executes it. They communicate only through the shared database file.
- **Targeted Concurrency:** Provides clear, mandatory choices for both I/O-bound (thread) and CPU-bound (process) concurrency models on a per-task basis.

## Installation

```bash
pip install castor-io
```

## Quickstart

### 1.  Create your application file

```python
# main.py
import time
from beaver import BeaverDB
from castor import Manager

# 1. Setup the manager
db = BeaverDB("tasks.db")
manager = Manager(db)

# 2. Define a background task
@manager.task(mode='thread')
def send_email(recipient: str):
    """Simulates a background I/O task."""
    print(f"-> Sending email to {recipient}...")
    time.sleep(2)
    print(f"<- Email sent to {recipient}.")
    return {"recipient": recipient, "status": "sent"}

# 3. Dispatch the task (if running this file directly)
if __name__ == "__main__":
    print("--- Dispatching background task ---")
    email_task = send_email.delay("alice@example.com")
    print(f"Dispatched email task with ID: {email_task.id}")

    print("\n--- Waiting for result ---")
    result = email_task.join(timeout=5)
    print(f"Result from email task: {result}")
    print("\n--- Example finished ---")
```

### 2. Run the worker from your terminal

The worker needs to know where your `Manager` instance is. You provide this as an import path.

```bash
castor main:manager
```

You will see the worker start and process the task.

```
Starting server... Ctrl+C to stop.
```

Alternatively, run in interactive mode to see a rich dashboard with logs and statistics.

```bash
castor main:manager -i
```

### 3. Run your application

```bash
python main.py
```

You will see tasks being processed in the worker log.

## Features

- **Task Decorator:** A simple `@manager.task` decorator to turn any function into a background task.
- **Execution Modes:** Explicitly define tasks as `thread` (for I/O-bound work) or `process` (for CPU-bound work).
- **Task Handle:** Calling `.delay()` on a task returns a `TaskHandle` object, allowing you to check the `.status()` or wait for the result.
- **Synchronous and Asynchronous Results:** Block for a result with `.join()` or wait for it asynchronously with `.resolve()`.
- **Reliable Backend:** Uses `beaver-db` for a simple and reliable file-based persistence layer.
- **CLI Worker:** A built-in command-line interface to run the worker server.

## Roadmap

This is a work in progress. The immediate roadmap includes:

- [x] **Process-based tasks:** While the `mode='process'` is available in the API, the underlying process pool execution is not yet fully implemented. This is the highest priority.
- [x] **Monitoring UI:** A more advanced terminal-based monitoring dashboard for the worker.
- [ ] **Retries and error handling:** More robust mechanisms for automatic retries and dead-letter queues.
