from app.breaker import CircuitBreaker
from conftest import FakeClock


def test_opens_only_after_threshold_consecutive_failures():
    clock = FakeClock()
    b = CircuitBreaker(threshold=3, cooldown_s=60, clock=clock)
    for _ in range(2):
        b.record_failure("m")
    assert not b.is_open("m")
    b.record_failure("m")
    assert b.is_open("m")


def test_success_resets_the_count():
    b = CircuitBreaker(threshold=2, cooldown_s=60, clock=FakeClock())
    b.record_failure("m")
    b.record_success("m")
    b.record_failure("m")
    assert not b.is_open("m")


def test_half_open_after_cooldown_then_reopens_on_failure():
    clock = FakeClock()
    b = CircuitBreaker(threshold=2, cooldown_s=60, clock=clock)
    b.record_failure("m")
    b.record_failure("m")
    clock.now += 59
    assert b.is_open("m")
    clock.now += 2
    assert not b.is_open("m")          # one trial allowed
    b.record_failure("m")              # trial fails
    assert b.is_open("m")              # open for a full cool-down again
    clock.now += 61
    b.record_success("m")
    assert not b.is_open("m")


def test_models_are_independent():
    b = CircuitBreaker(threshold=1, cooldown_s=60, clock=FakeClock())
    b.record_failure("a")
    assert b.is_open("a") and not b.is_open("b")
    assert b.snapshot()["a"] == {"open": True, "consecutive_failures": 1}
