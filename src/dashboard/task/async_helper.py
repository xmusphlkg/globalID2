"""Async helper utilities for task operations in Streamlit.

This module provides a persistent event loop in a background thread
for executing async operations from Streamlit's synchronous context.
"""
import asyncio
import atexit
import threading
from typing import Any, Coroutine
from functools import wraps


class _AsyncExecutor:
    """Singleton that maintains a persistent event loop in a background thread."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize the background thread and event loop."""
        self._loop = None
        self._thread = None
        self._start_loop()
        atexit.register(self._cleanup)
    
    def _start_loop(self):
        """Start the event loop in a background thread."""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        
        self._thread = threading.Thread(
            target=run_loop,
            name="AsyncHelper-EventLoop",
            daemon=True
        )
        self._thread.start()
        
        # Wait for loop to be ready
        while self._loop is None:
            threading.Event().wait(0.01)
    
    def run_async(self, coro: Coroutine) -> Any:
        """Execute a coroutine in the background event loop.
        
        Args:
            coro: Async coroutine to execute
            
        Returns:
            Result from the coroutine
        """
        if self._loop is None or not self._loop.is_running():
            self._start_loop()
        
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()
    
    def _cleanup(self):
        """Clean up the event loop on shutdown."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)


# Global executor instance
_executor = _AsyncExecutor()


def run_async(coro: Coroutine) -> Any:
    """Run an async coroutine using the persistent background event loop.
    
    This function uses a singleton event loop running in a background thread,
    which avoids the "event loop closed" errors that occur when creating
    and destroying event loops repeatedly.
    
    Args:
        coro: Async coroutine to execute
        
    Returns:
        Result from the coroutine
    """
    return _executor.run_async(coro)


def async_cached(ttl: int = 300):
    """Decorator to cache async function results.
    
    Args:
        ttl: Time to live in seconds
    """
    def decorator(func):
        cache = {}
        cache_time = {}
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            key = str(args) + str(kwargs)
            current_time = time.time()
            
            # Check cache first
            if key in cache and (current_time - cache_time[key]) < ttl:
                return cache[key]
            
            # Call the async function and cache result
            result = run_async(func(*args, **kwargs))
            cache[key] = result
            cache_time[key] = current_time
            return result
        
        # Add cache clearing method
        wrapper.clear_cache = lambda: cache.clear()
        return wrapper
    return decorator
