"""Global token-bucket rate limiter for GLEIF API calls."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future
from typing import Any, Callable


class RateLimiter:
    """Token-bucket rate limiter with an async work queue.

    All GLEIF API calls from all sessions are funnelled through a single
    instance so the aggregate request rate stays within the GLEIF limit
    (~60 req/min).

    Parameters
    ----------
    rate : float
        Tokens added per second (1.0 = 60/min).
    capacity : int
        Maximum burst size (tokens that can accumulate).
    """

    def __init__(self, rate: float = 1.0, capacity: int = 3) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._queue: asyncio.Queue[tuple[Callable[..., Any], tuple[Any, ...], asyncio.Future[Any]]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background worker that drains the queue."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Cancel the background worker and drain pending items."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, func: Callable[..., Any], *args: Any) -> Any:
        """Enqueue a **sync** function and return its result.

        The function will be run in the default executor once a token is
        available, ensuring the global rate limit is respected.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        await self._queue.put((func, args, fut))
        return await fut

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def _wait_for_token(self) -> None:
        """Block until at least one token is available."""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Sleep until roughly one token would be available
            deficit = 1.0 - self._tokens
            await asyncio.sleep(deficit / self._rate)

    async def _worker(self) -> None:
        """Background loop: pull items from the queue, wait for a token,
        run the function in the thread-pool executor."""
        loop = asyncio.get_running_loop()
        while True:
            func, args, fut = await self._queue.get()
            try:
                await self._wait_for_token()
                result = await loop.run_in_executor(None, func, *args)
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.cancel()
                raise
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)


# ------------------------------------------------------------------
# Singleton access
# ------------------------------------------------------------------

_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Return the global RateLimiter singleton (created on first call)."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
