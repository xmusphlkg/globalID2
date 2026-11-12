"""Service-layer exceptions."""


class TaskCancelledError(RuntimeError):
    """Raised when a background task has been cancelled by the user."""


class TaskExecutionSupersededError(TaskCancelledError):
    """An explicit retry replaced this execution; it must not update the new run."""
