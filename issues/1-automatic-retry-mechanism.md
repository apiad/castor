---
number: 1
title: "Automatic retry mechanism"
state: open
labels:
---

## Problem

Currently, tasks that fail due to transient errors (e.g., network timeouts, temporary API unavailability) are marked as permanently failed. This requires users to implement complex and repetitive retry logic inside every task function, which is cumbersome and not robust for production use.

## Proposed Solution
Implement a built-in, automatic retry mechanism in the Castor worker. The retry policy should be configurable when a task is dispatched, allowing the library to handle transient failures gracefully without requiring boilerplate code in user tasks.

This moves the responsibility for handling transient errors from the user's application logic into the task runner itself.

## Proposed API
The retry policy will be defined by adding optional parameters to the .submit() method:

```python
from requests.exceptions import Timeout

# The user can define the retry policy at the call site
my_task.submit(
    # regular arguments...
    retries=3,
    backoff=5,
    retry_on=(Timeout,)
)
```

- **retries**: Number of times to retry after the first failure.
- **backoff**: Initial delay before the first retry. Subsequent retries will use exponential backoff.
- **retry_on**: A tuple of exception classes that should trigger a retry.

## High-Level Implementation Plan

- Extend `Task` model (core.py): Add optional fields to store retry state (retries, backoff_seconds, current_attempt, retry_on).
- Update `.submit()` (core.py): Modify the method to accept and store the new retry parameters in the `Task` object.
- Implement retry logic (server.py): In the `_fail_task` function, check the task's retry policy. If a retry is warranted, re-schedule the task with a calculated backoff. Otherwise, mark it as permanently failed.
