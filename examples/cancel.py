import time
from beaver import BeaverDB
from castor import Manager

# --- 1. Setup ---
db = BeaverDB("tasks.db")
manager = Manager(db)


# --- 2. Cancellable Task Definition ---

@manager.task(mode='thread', cancellable=True)
def long_running_report():
    """
    A long-running task that can be cancelled.
    It yields at each step, allowing the worker to check for cancellation.
    """
    print("-> Report generation started. This will take 10 seconds.")
    total_steps = 10
    for i in range(total_steps):
        print(f"   ...processing step {i + 1}/{total_steps}")
        time.sleep(1)
        yield  # This is the cooperative cancellation checkpoint

    print("<- Report generation finished without cancellation.")
    return {"status": "complete", "steps": total_steps}


# --- 3. Task Dispatch and Cancellation ---

if __name__ == "__main__":
    print("--- Dispatching a cancellable background task ---")
    print("Run the worker in another terminal to see it run:")
    print("castor examples.cancel:manager")

    task = long_running_report.submit()
    print(f"Dispatched report task with ID: {task.id}")

    # Let the task run for a few seconds
    print("\n--- Letting the task run for 4 seconds... ---")
    time.sleep(4)

    # Now, request the task to be cancelled
    print("\n--- Requesting task cancellation... ---")
    task.cancel()

    print("\n--- Example finished ---")
    print("Check the worker's output to see the cancellation message.")
