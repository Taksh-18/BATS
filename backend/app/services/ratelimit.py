import time
from collections import defaultdict, deque


class FailureLimiter:
    """Sliding-window failure counter. In-memory, so per process: run a shared store
    (e.g. Redis) instead if the API is ever deployed with several workers."""

    def __init__(self, max_failures: int, window_seconds: int):
        self.max_failures = max_failures
        self.window = window_seconds
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed; 0 if allowed now."""
        now = time.monotonic()
        q = self._trim(key, now)
        if len(q) < self.max_failures:
            return 0
        return int(self.window - (now - q[0])) + 1

    def fail(self, key: str) -> bool:
        """Record a failure; True if this one triggered the lockout."""
        now = time.monotonic()
        if len(self._fails) > 10_000:  # bound memory against attackers inventing keys
            for k in [k for k in self._fails if not self._trim(k, now)]:
                del self._fails[k]
        q = self._trim(key, now)
        q.append(now)
        return len(q) == self.max_failures

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
