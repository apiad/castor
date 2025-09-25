# **Final Design Plan: Castor \- A Micro Background Task Library**

Version: 1.0  
Date: September 25, 2025  
Status: Finalized

## **1\. Core Philosophy**

* **Explicit Over Implicit:** The library avoids global state. Configuration is handled through explicit objects (Manager), making applications more robust, testable, and easier to reason about.  
* **Single Responsibility:** castor is a task queueing and execution library. beaver-db is its database backend. The two are composed, not conflated.  
* **Developer Experience:** The API is designed to be intuitive, requiring minimal boilerplate to turn a function into a background task.  
* **Decoupled Architecture:** The application that enqueues a task is fully separate from the worker process that executes it. They communicate only through the shared beaver-db file, ensuring reliability.  
* **Targeted Concurrency:** Provides clear, mandatory choices for both I/O-bound (thread) and CPU-bound (process) concurrency models on a per-task basis.

## **2\. Key Components**

1. **The Manager Object:** The central object from the castor library. It holds the beaver-db database connection and serves as the entry point for all library functions, including the task decorator.  
2. **The Decorator (@manager.task):** A method of a Manager instance. It wraps a standard Python function, registering it as a discoverable task with a specific execution mode.  
3. **The Task Handle:** A future/promise object returned by the .delay() method. It acts as a handle to the enqueued task, containing its unique ID and methods for status checking and result retrieval.  
4. **The Beaver-DB Backend:** The persistence and communication layer. It is used for:  
   * A **castor\_tasks table**: A document store for the state of every task.  
   * A **pending\_tasks queue**: The central queue where new tasks are placed for workers.  
   * A **results::{task\_id} queue**: A dedicated, per-task queue for efficiently delivering its result.  
5. **The Worker (castor CLI):** A standalone command-line application. It discovers tasks and configuration from the user's Manager instance, executes tasks from the queue, and provides a rich monitoring interface.

## **3\. User-Facing API**

### **3.1. Initialization**

Initialization is done by instantiating beaverdb.Database and then passing that instance to a castor.Manager. This manager object becomes the single source of truth.

\# main.py (Application Entry Point)  
import beaverdb  
import castor

\# 1\. Create a database instance  
db \= beaverdb.Database(db\_path="/var/data/my\_app\_tasks.db")

\# 2\. Create a Manager instance, which holds the db connection  
manager \= castor.Manager(db)

\# The 'manager' object is now the entry point for all task operations  
\# and should be imported by other modules.

### **3.2. Defining Tasks**

The @task decorator is called on your Manager instance. Any file defining tasks must import this specific instance.

**Decorator Arguments:**

* mode: (Mandatory) A string that must be either 'thread' or 'process'.  
* daemon=False (default): The task is considered critical. The worker will attempt a graceful shutdown, waiting for this task to complete.  
* daemon=True: The task is non-critical and can be terminated immediately if the worker process exits.

\# tasks.py  
from main import manager \# Import the manager instance from the entry point  
import time

@manager.task(mode='process')  
def complex\_calculation(a, b):  
    """A CPU-bound task that must run in a separate process."""  
    time.sleep(2) \# Simulate heavy computation  
    return a \+ b

@manager.task(mode='thread', daemon=False)  
def process\_payment(order\_id: str):  
    """A critical I/O-bound task that must be allowed to finish."""  
    print(f"Processing payment for {order\_id}")  
    time.sleep(1) \# Simulate network latency  
    return {"status": "ok", "processed\_order": order\_id}

### **3.3. Enqueuing Tasks**

Call the .delay() method on the decorated function. This is a non-blocking call that serializes the task and places it on the pending\_tasks queue. It immediately returns a Task handle.

\# api/routes.py  
from tasks import complex\_calculation

def start\_background\_job():  
    task\_handle \= complex\_calculation.delay(10, 20\)  
    print(f"Dispatched calculation task with ID: {task\_handle.id}")  
    return task\_handle

### **3.4. The Task Handle API**

The Task object is your handle to the task's lifecycle and result. To retrieve a handle for a pre-existing task, use the manager.

\# app\_logic.py  
from main import manager

task\_handle \= manager.get\_task\_by\_id("c4a3f2b1-d5b4-c3a2-b1a0-e6c5d4b3a2f1")

\# \--- Properties \---  
print(f"Task ID is: {task\_handle.id}") \# A unique UUID string

\# \--- Methods \---  
\# Get current status: "pending" | "running" | "success" | "failed"  
current\_status \= task\_handle.status()

\# Block execution until the task is complete and return its result.  
\# Can raise TimeoutError or the task's exception.  
result \= task\_handle.join(timeout=5)

\# Asynchronously wait for the task to complete and return its result.  
result \= await task\_handle.resolve(timeout=5)

\# \--- Magic Methods \---  
\# Check if the task is finished (status is "success" or "failed").  
if task\_handle:  
    print("Task is complete.")  
else:  
    print("Task is still pending or running.")

## **4\. Worker & Command-Line Interface**

The worker is a standalone application, invoked from the terminal. Its configuration is derived entirely from the specified Manager instance.

### **4.1. Invocation**

castor \--workers 4 \--threads 8 main:manager

**Arguments:**

* \--workers N: (Optional) The number of processes in the pool for mode='process' tasks. Defaults to the number of CPU cores.  
* \--threads M: (Optional) The number of threads in the pool for mode='thread' tasks. Defaults to workers \* 2\.  
* main:manager: **Required.** The Python import path to your Manager instance, in the format \<module\_path\>:\<variable\_name\>. This is the single source of truth for the worker, providing both the database connection and the registered task functions.

### **4.2. Monitoring UI**

When run in a terminal, the castor worker will display a live dashboard powered by a library like rich or textual. This provides at-a-glance visibility into the system's health.

 C Λ S T O R v1.0 | Manager: main:manager | Status: Running   
\+--------------------------------+--------------------------------------+  
|           Worker Stats         |          Latest Tasks                |  
\+--------------------------------+--------------------------------------+  
| Process Tasks: \[bold green\]3 / 4\[/\]            | \[dim\]c4a3f2b1\[/\] \[green\]SUCCESS\[/\] process\_payment  |  
| Thread Tasks:  \[bold green\]7 / 8\[/\]            | \[dim\]d5b4c3a2\[/\] \[yellow\]RUNNING\[/\] complex\_calc   |  
| Pending Queue: \[bold cyan\]12\[/\]                | \[dim\]e6c5d4b3\[/\] \[red\]FAILED\[/\]  generate\_thumb   |  
| Total Done:    \[bold\]1,234\[/\]              | \[dim\]f7d6e5c4\[/\] \[cyan\]PENDING\[/\] send\_email       |  
\+--------------------------------+--------------------------------------+  
| Logs (UTC)                                                           |  
\+----------------------------------------------------------------------+  
| INFO     Task d5b4c3a2 started: complex\_calculation(10, 20\) |  
| INFO     Task c4a3f2b1 finished successfully in 1.02s       |  
| ERROR    Task e6c5d4b3 failed: FileNotFoundError           |  
\+----------------------------------------------------------------------+

## **5\. beaver-db Schema Design**

The database schema is designed for efficiency and clarity.

beaver-db file: /var/data/my\_app\_tasks.db

├── Table: 'castor\_tasks' (Document Store)  
│   └── Document Key: {task\_id} (UUID string)  
│       └── Value (dict):  
│           {  
│               "id": "...",  
│               "task\_name": "tasks.complex\_calculation",  
│               "status": "pending" | "running" | "success" | "failed",  
│               "args": \[10, 20\],  
│               "kwargs": {},  
│               "enqueued\_at": "2025-09-25T16:04:14Z",  
│               "started\_at": null | "...",  
│               "finished\_at": null | "...",  
│               "result": null | \<serialized\_result\>,  
│               "error": null | "Traceback..."  
│           }  
│  
├── Queue: 'pending\_tasks'  
│   └── Entry: {"task\_id": "...", "task\_name": "...", "args": \[\], "kwargs": {}}  
│  
└── Queue: 'results::{task\_id}' (One dedicated queue for each task's result)  
    └── Entry: \<serialized\_result\_or\_error\_object\>

## **6\. Full Example with FastAPI**

This example demonstrates the complete, recommended structure for a web application.

**File: main.py** (Your application entry point)

import beaverdb  
import castor  
from fastapi import FastAPI  
from pydantic import BaseModel

\# \--- Core Application Objects \---  
db \= beaverdb.Database(db\_path="/var/data/my\_app\_tasks.db")  
manager \= castor.Manager(db)  
app \= FastAPI()

\# Import task definitions AFTER manager is created to ensure they register  
from tasks import complex\_calculation

\# \--- API Models and Endpoints \---  
class TaskResponse(BaseModel):  
    task\_id: str  
    status: str  
    result: dict | str | int | None \= None

@app.post("/calculate", response\_model=TaskResponse, status\_code=202)  
async def calculate\_endpoint():  
    """ Enqueues a task and returns its handle immediately. """  
    task\_handle \= complex\_calculation.delay(100, 250\)  
    return TaskResponse(task\_id=task\_handle.id, status=task\_handle.status())

@app.get("/tasks/{task\_id}", response\_model=TaskResponse)  
async def get\_task\_status(task\_id: str):  
    """ Polls for the status and result of a task. """  
    task\_handle \= manager.get\_task\_by\_id(task\_id)  
      
    if not task\_handle:  
        return {"error": "Task not found"}, 404  
      
    status \= task\_handle.status()  
    result \= None  
    if task\_handle: \# Check if finished  
        try:  
            \# Task is done, so join() will return immediately without blocking.  
            result \= task\_handle.join(timeout=0.1)   
        except Exception as e:  
            result \= f"Task failed: {str(e)}"

    return TaskResponse(task\_id=task\_handle.id, status=status, result=result)

**File: tasks.py** (Your task definitions)

import time  
\# Import the manager instance from your main application file  
from main import manager

@manager.task(mode='process')  
def complex\_calculation(a, b):  
    time.sleep(2)  
    return a \+ b  
