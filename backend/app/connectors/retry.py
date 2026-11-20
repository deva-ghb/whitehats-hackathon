"""The one retry and rate-limit helper every connector uses: bounded attempts, backoff, and the provider's own wait hints."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

ATTEMPTS = 4
BASE_DELAY_S = 1.0
MAX_DELAY_S = 30.0
# AWS error codes that mean "slow down", not "you may not".
AWS_THROTTLING = frozenset({
    "Throttling", "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded",
    "ServiceUnavailable", "SlowDown",
})


class Throttled(Exception):
    """An HTTP source said to wait (429, or 503 with Retry-After)."""

    def __init__(self, wait_s: float):
        super().__init__(f"asked to wait {wait_s:g}s")
        self.wait_s = wait_s


def aws_wait_hint(exc: Exception) -> float | None:
    """0 for a throttling error (use backoff), None for anything that retrying cannot fix."""
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return 0.0 if code in AWS_THROTTLING else None


def http_wait_hint(exc: Exception) -> float | None:
    return exc.wait_s if isinstance(exc, Throttled) else None


def with_retries[T](call: Callable[[], T], wait_hint: Callable[[Exception], float | None], *,
                 attempts: int = ATTEMPTS, sleep: Callable[[float], None] = time.sleep) -> T:
    """Run call; on an error wait_hint calls retryable, wait and try again, up to attempts in all."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            hint = wait_hint(exc)
            if hint is None or attempt == attempts:
                raise
            sleep(min(max(hint, BASE_DELAY_S * 2 ** (attempt - 1)), MAX_DELAY_S))
    raise AssertionError("unreachable: the last attempt returns or raises")


class Pacer:
    """Keeps calls at least min_interval_s apart, below a provider's published request rate.

    Safe to share between threads: each caller is given the next free slot, then waits for it.
    """

    def __init__(self, min_interval_s: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval_s, self._clock, self._sleep = min_interval_s, clock, sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            slot = now if self._last is None else max(now, self._last + self.min_interval_s)
            self._last = slot
        if slot > now:
            self._sleep(slot - now)
