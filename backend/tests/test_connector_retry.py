"""The shared retry and pacing helper: bounded, backs off, honours the provider's wait, never retries a refusal."""

import pytest
from botocore.exceptions import ClientError

from app.connectors.retry import (
    ATTEMPTS,
    MAX_DELAY_S,
    Pacer,
    Throttled,
    aws_wait_hint,
    http_wait_hint,
    with_retries,
)


def _aws(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "FilterLogEvents")


class Flaky:
    def __init__(self, *errors: Exception):
        self.errors, self.calls = list(errors), 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "read"


def test_throttling_backs_off_and_then_succeeds():
    waits = []
    call = Flaky(_aws("ThrottlingException"), _aws("TooManyRequestsException"))
    assert with_retries(call, aws_wait_hint, sleep=waits.append) == "read"
    assert waits == [1.0, 2.0]


def test_a_refusal_is_not_retried():
    waits = []
    call = Flaky(_aws("AccessDeniedException"))
    with pytest.raises(ClientError):
        with_retries(call, aws_wait_hint, sleep=waits.append)
    assert (call.calls, waits) == (1, [])


def test_attempts_are_bounded():
    waits = []
    call = Flaky(*[_aws("Throttling")] * (ATTEMPTS + 2))
    with pytest.raises(ClientError):
        with_retries(call, aws_wait_hint, sleep=waits.append)
    assert call.calls == ATTEMPTS
    assert len(waits) == ATTEMPTS - 1


def test_the_providers_wait_is_honoured_and_capped():
    waits = []
    call = Flaky(Throttled(7), Throttled(600))
    assert with_retries(call, http_wait_hint, sleep=waits.append) == "read"
    assert waits == [7, MAX_DELAY_S]


def test_the_pacer_keeps_calls_apart():
    now, waits = [100.0], []

    def sleep(seconds):
        waits.append(round(seconds, 3))
        now[0] += seconds

    pacer = Pacer(0.2, clock=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] += 0.05
    pacer.wait()
    now[0] += 1.0
    pacer.wait()
    assert waits == [0.15]


def test_threads_sharing_a_pacer_each_get_their_own_slot():
    """Three callers arriving in the same instant are spaced 0.2 s apart, not let through together."""
    waits = []
    pacer = Pacer(0.2, clock=lambda: 100.0, sleep=lambda seconds: waits.append(round(seconds, 3)))
    for _ in range(3):
        pacer.wait()
    assert waits == [0.2, 0.4]
