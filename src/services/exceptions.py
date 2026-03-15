"""Service-layer exceptions."""


class TaskCancelledError(RuntimeError):
    """Raised when a background task has been cancelled by the user."""
