import time

from beaver import BeaverDB
from castor import Manager

# --- 1. Setup ---
# This is the central setup for your application.
# The manager instance should be imported by any part of your app
# that needs to define or dispatch tasks.
DB_PATH = "tasks.db"

db = BeaverDB(DB_PATH)
manager = Manager(db)


# --- 2. Task Definitions ---

@manager.task(mode='thread')
def send_email(recipient: str, subject: str):
    """
    Simulates an I/O-bound task like sending an email.
    Running this in a thread is efficient as it mostly waits for the network.
    """
    print(f"-> Starting to send email to {recipient}...")
    time.sleep(2)  # Simulate network latency
    print(f"<- Finished sending email to {recipient}.")
    return {"recipient": recipient, "subject": subject, "status": "sent"}


@manager.task(mode='process')
def cpu_intensive_task(a: int, b: int):
    """
    Simulates a CPU-bound task.
    Running this in a separate process prevents it from blocking the main
    application or other I/O-bound tasks.
    """
    print(f"-> Starting CPU-intensive calculation for {a} + {b}...")
    # Simulate heavy work without sleeping
    _ = [i * i for i in range(1000_000)]
    print(f"<- Finished CPU-intensive calculation.")
    return a + b


# --- 3. Task Dispatch and Result Retrieval ---

if __name__ == "__main__":
    print("--- Dispatching background tasks ---")

    # Dispatch tasks using the .delay() method.
    # This is a non-blocking call that immediately returns a TaskHandle.
    email_task_1 = send_email.submit("alice@example.com", "Meeting Reminder")
    calc_task_1 = cpu_intensive_task.submit(100, 200)
    email_task_2 = send_email.submit("bob@example.com", "Weekly Report")

    print(f"Dispatched email task 1 with ID: {email_task_1.id}")
    print(f"Dispatched calculation task with ID: {calc_task_1.id}")
    print(f"Dispatched email task 2 with ID: {email_task_2.id}")

    print("\n--- Waiting for results ---")
    print("This will block until the tasks are completed by a worker.")

    # Use the .join() method on the handle to block and wait for the result.
    # The timeout is optional but good practice.
    try:
        email_result = email_task_1.join(timeout=10)
        print(f"\nResult from email task 1: {email_result}")

        calc_result = calc_task_1.join(timeout=10)
        print(f"Result from calculation task: {calc_result}")
    except TimeoutError:
        print("A task timed out!")

    print("\n--- Example finished ---")
