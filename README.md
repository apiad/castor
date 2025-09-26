# Castor

<!-- Project badges -->
![PyPI - Version](https://img.shields.io/pypi/v/castor-io)
![PyPi - Python Version](https://img.shields.io/pypi/pyversions/castor-io)
![Github - Open Issues](https://img.shields.io/github/issues-raw/apiad/castor)
![PyPi - Downloads (Monthly)](https://img.shields.io/pypi/dm/castor-io)
![Github - Commits](https://img.shields.io/github/commit-activity/m/apiad/castor)

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

Castor's API is designed to scale with your project's complexity. You can start with a simple, self-contained script and later refactor to a more robust, decoupled architecture without changing your task logic.

### Mode 1: Simple (For Scripts and Prototypes)

This is the fastest way to get started. It's perfect for single-file applications, prototypes, or simple scripts where everything is in one place.

#### 1. Create your script

In this mode, you create a Manager instance and use its @manager.task decorator to define and immediately bind your tasks.

```
# my_script.py
import time
from beaver import BeaverDB
from castor import Manager

# 1. Setup the manager
db = BeaverDB("tasks.db")
manager = Manager(db)

# 2. Define a background task directly on the manager instance
@manager.task(mode='thread')
def send_email(recipient: str):
    """Simulates a background I/O task."""
    print(f"-> Sending email to {recipient}...")
    time.sleep(2)
    print(f"<- Email sent to {recipient}.")
    return {"recipient": recipient, "status": "sent"}

# 3. Dispatch the task and wait for the result
if __name__ == "__main__":
    print("--- Dispatching background task ---")
    email_task = send_email.submit("alice@example.com")
    print(f"Dispatched email task with ID: {email_task.id}")

    print("\n--- Waiting for result ---")
    result = email_task.join(timeout=5)
    print(f"Result from email task: {result}")
```

#### 2. Run the worker & your script

You'll need two separate terminal windows.

In Terminal 1, run the worker: The worker needs the import path to your manager instance.

```bash
castor my_script:manager
```

In Terminal 2, run your script:

```bash
python my_script.py
```

You will see the task being dispatched in your script and processed in the worker's log.

### Mode 2: Decoupled (For Applications)

As your application grows, you'll want to separate your business logic (tasks) from your application's infrastructure (the manager). This mode is designed for maintainability, testability, and reusability.

#### 1. Define your tasks in their own module

Use the global @task decorator. This creates unbound "blueprints" of your tasks that are not tied to any specific database or manager.

```python
# tasks.py
import time
from castor import task

@task(mode='thread')
def send_email(recipient: str):
    """An I/O-bound task."""
    print(f"-> Sending email to {recipient}...")
    time.sleep(2)
    return {"status": "sent"}

@task(mode='process')
def generate_report(month: str):
    """A CPU-bound task."""
    print(f"-> Generating report for {month}...")
    time.sleep(3)
    return {"report_size_mb": 50}
```

#### 2. Create and configure the manager in your main application file

The Manager discovers and binds the tasks from your tasks module, making them live.

```python
# main.py
from beaver import BeaverDB
from castor import Manager
import tasks # Import the module containing your task blueprints

# Setup the database and manager
db = BeaverDB("prod_tasks.db")
# The manager discovers and binds the tasks from the `tasks` module.
manager = Manager(db, tasks=[tasks])

# This manager instance would typically be passed to your web framework
# or other parts of your application.
```

#### 3. Dispatch tasks from anywhere

Now you can import your tasks directly and they will be correctly routed through the manager you configured.

```python
# api.py
from tasks import send_email, generate_report

def handle_new_user_signup(email_address: str):
    print("User signed up, dispatching welcome email.")
    # This call is automatically routed through the configured manager
    send_email.submit(email_address)

def handle_end_of_month():
    print("End of month, dispatching report generation.")
    generate_report.submit("September")

# In a real app, these functions would be called from web endpoints.
if __name__ == "__main__":
    handle_new_user_signup("bob@example.com")
    handle_end_of_month()
```

#### 4. Run the worker & your application

In Terminal 1, run the worker: Point it to the configured manager in your main file.

```bash
castor main:manager -i # Using interactive mode for a nice dashboard
```

In Terminal 2, run your application logic:

```bash
fastapi run api:app
```

## Features

- **Dual-Mode API**: Use the simple `@manager.task` decorator for one-off scripts or the decoupled `@task` decorator for modular, scalable applications.
- **Execution Modes:** Explicitly define tasks as `thread` (for I/O-bound work) or `process` (for CPU-bound work).
- **Task Handle:** Calling `.submit()` on a task returns a `TaskHandle` object, allowing you to check the `.status()` or wait for the result.
- **Scheduled and Recurring Tasks**: Dispatch tasks to run at a specific time in the future using `at` or after a certain `delay`. Create recurring tasks that run `every` X seconds, for a specific number of `times`, or `until` a certain date.
- **Synchronous and Asynchronous Results:** Block for a result with `.join()` or wait for it asynchronously with `.resolve()`.
- **Reliable Backend:** Uses `beaver-db` for a simple and reliable file-based persistence layer.
- **CLI Worker:** A built-in command-line interface to run the worker server.

## Roadmap

Castor is actively being developed. The immediate roadmap is focused on stability and developer experience:

- **Retries and Error Handling**: Implementing robust mechanisms for automatic task retries with configurable backoff strategies and support for dead-letter queues.
- **More Examples**: Adding a wider variety of examples to the documentation, showcasing how to integrate Castor with popular web frameworks and other real-world scenarios.
- **Comprehensive Unit Tests**: Increasing the test coverage to ensure all features are reliable and to prevent regressions.
