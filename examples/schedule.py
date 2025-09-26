# examples/scheduled_tasks.py

import time
from datetime import datetime, timezone, timedelta

from beaver import BeaverDB
from castor import Manager

# --- 1. Setup ---
# Initialize the database and the Castor manager.
db = BeaverDB("tasks.db")
manager = Manager(db)


# --- 2. Task Definition ---

@manager.task(mode='thread')
def log_message(message: str):
    """
    A simple task that prints a message to the console, showing the current time.
    """
    # Return a simple result
    return {"status": "ok", "message_logged": message}


# --- 3. Task Dispatch and Result Retrieval ---

if __name__ == "__main__":
    print("--- Dispatching various background tasks ---")

    # Scenario 1: Run a task immediately.
    # This is the default behavior with no scheduling parameters.
    immediate_task = log_message.submit("This task runs right away.")
    print(f"Dispatched immediate task with ID: {immediate_task.id}")

    # Scenario 2: Schedule a task to run in the future.
    # We'll use the `delay` parameter to run it in 10 seconds.
    future_time = datetime.now(timezone.utc) + timedelta(seconds=10)
    scheduled_task = log_message.submit(
        "This task is scheduled for 10 seconds in the future.",
        delay=10
    )
    print(f"Dispatched scheduled task with ID: {scheduled_task.id} to run at ~{future_time.strftime('%H:%M:%S')}")


    # Scenario 3: Repeat a task 5 times, with a 3-second interval.
    # The `every` parameter sets the interval, and `times` sets the repetition count.
    # Note: The `submit` call only returns a handle for the *first* execution.
    repeating_task = log_message.submit(
        "This task will repeat.",
        every=3,  # Repeat every 3 seconds
        times=5,  # Repeat a total of 5 times
    )
    print(f"Dispatched repeating task with ID: {repeating_task.id}")

    print("\n--- Waiting for the first task's result ---")
    print("Run the worker in another terminal to see tasks being processed:")
    print("castor examples.scheduled_tasks:manager")

    # We can still use .join() on the handles to wait for their completion.
    try:
        result = immediate_task.join(timeout=5)
        print(f"\nResult from immediate task: {result}")
    except TimeoutError:
        print("Immediate task timed out!")

    print("\n--- Example finished ---")
    print("Keep the worker running to see the scheduled and repeated tasks execute.")