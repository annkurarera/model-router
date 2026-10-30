"""Per-client rate limits: requests per minute and requests per day (in memory).

The OpenAI budget limit and the router's daily USD budget are the real safety nets;
these counters reset when the server restarts.
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone


class RateLimited(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int, clock=time.time):
        self.per_minute = per_minute
        self.per_day = per_day
        self._clock = clock
        self._recent: dict[str, deque] = defaultdict(deque)
        self._daily: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def check(self, client: str) -> None:
        """Counts one request for `client`, or raises RateLimited without counting it."""
        now = self._clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._lock:
            window = self._recent[client]
            while window and now - window[0] >= 60:
                window.popleft()
            if self.per_minute and len(window) >= self.per_minute:
                raise RateLimited("rate_limited", f"More than {self.per_minute} requests per minute. Try again shortly.")
            if self.per_day and self._daily[(client, day)] >= self.per_day:
                raise RateLimited("daily_limit_reached", f"Daily limit of {self.per_day} requests reached.")
            window.append(now)
            self._daily[(client, day)] += 1
