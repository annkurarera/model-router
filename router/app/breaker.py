"""Circuit breaker: after N consecutive failures a model is skipped for a cool-down period.

After the cool-down one trial call is allowed (half-open). Success closes the breaker;
another failure opens it again for a full cool-down.
"""
import threading
import time
from dataclasses import dataclass


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown_s: float = 60.0, clock=time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._states: dict[str, _State] = {}
        self._lock = threading.Lock()

    def is_open(self, key: str) -> bool:
        """True while the model should be skipped."""
        with self._lock:
            state = self._states.get(key)
            if state is None or state.opened_at is None:
                return False
            return self._clock() - state.opened_at < self.cooldown_s

    def record_success(self, key: str) -> None:
        with self._lock:
            self._states[key] = _State()

    def record_failure(self, key: str) -> None:
        with self._lock:
            state = self._states.setdefault(key, _State())
            state.failures += 1
            if state.failures >= self.threshold:
                state.opened_at = self._clock()

    def snapshot(self) -> dict[str, dict]:
        """Current state per model, for /api/health."""
        with self._lock:
            keys = list(self._states)
        return {k: {"open": self.is_open(k), "consecutive_failures": self._states[k].failures} for k in keys}
