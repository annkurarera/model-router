"""Failure scenarios for the bounded executor (plan: Testing plan > Failure scenarios)."""
import pytest

from app.executor import ErrorCategory, InvalidResponse, StopReason, classify_error
from conftest import Empty, Slow, err

MINI, MINI5, FULL, HIGH = "openai/gpt-4.1-mini", "openai/gpt-5-mini", "openai/gpt-4.1", "openai/gpt-5-mini"
CHAIN = ["gpt-4.1-mini", "gpt-5-mini-low", "gpt-4.1"]
MSG = [{"role": "user", "content": "Classify: my card was charged twice"}]


@pytest.mark.parametrize("kind, expected", [
    ("timeout", ErrorCategory.TIMEOUT),
    ("rate_limit", ErrorCategory.RATE_LIMIT),
    ("server", ErrorCategory.SERVER_ERROR),
    ("unavailable", ErrorCategory.SERVER_ERROR),
    ("connection", ErrorCategory.CONNECTION),
    ("auth", ErrorCategory.AUTH),
    ("bad_request", ErrorCategory.BAD_REQUEST),
    ("not_found", ErrorCategory.NOT_FOUND),
    ("context", ErrorCategory.CONTEXT_LENGTH),
])
def test_litellm_errors_are_classified(kind, expected):
    assert classify_error(err(kind)) == expected


def test_other_errors_are_classified():
    assert classify_error(InvalidResponse("empty")) == ErrorCategory.INVALID_RESPONSE
    assert classify_error(TimeoutError()) == ErrorCategory.TIMEOUT
    assert classify_error(ValueError("?")) == ErrorCategory.UNKNOWN


def test_success_on_primary_costs_its_tokens(make_executor):
    ex, provider = make_executor({MINI: ["Card dispute"]})
    r = ex.run_chain(CHAIN, MSG, max_tokens=200)
    assert r.ok and r.result.text == "Card dispute"
    assert r.fallbacks_used == 0 and r.calls_made == 1
    assert r.total_cost_usd == pytest.approx((100 * 0.40 + 50 * 1.60) / 1e6)
    assert provider.models_called() == [MINI]


def test_request_kwargs_respect_model_capabilities(make_executor):
    ex, provider = make_executor({MINI: [err("server"), err("server")]})
    ex.run_chain(CHAIN, MSG, max_tokens=200, temperature=0.2, response_format={"type": "json_object"})
    first, second = provider.calls[0], provider.calls[-1]
    assert first["temperature"] == 0.2 and "reasoning_effort" not in first and first["num_retries"] == 0
    assert second["model"] == MINI5 and "temperature" not in second
    assert second["reasoning_effort"] == "low" and second["max_tokens"] == 800  # thinking room
    assert second["response_format"] == {"type": "json_object"}


def test_transient_error_is_retried_once_then_succeeds(make_executor, clock):
    ex, provider = make_executor({MINI: [err("server"), "fine"]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and r.fallbacks_used == 0 and r.calls_made == 2
    assert [a.outcome for a in r.attempts] == ["error", "ok"] and r.attempts[1].retry
    assert clock.sleeps == [0.5]


def test_transient_error_twice_falls_back(make_executor):
    ex, provider = make_executor({MINI: [err("rate_limit"), err("rate_limit")]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and r.fallbacks_used == 1 and r.result.option.id == "gpt-5-mini-low"
    assert provider.models_called() == [MINI, MINI, MINI5]


@pytest.mark.parametrize("kind", ["auth", "bad_request"])
def test_non_retryable_errors_stop_the_whole_chain(make_executor, kind):
    ex, provider = make_executor({MINI: [err(kind)]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.NON_RETRYABLE and len(provider.calls) == 1


@pytest.mark.parametrize("kind", ["not_found", "context"])
def test_model_specific_errors_fall_back_without_retry(make_executor, kind):
    ex, provider = make_executor({MINI: [err(kind)]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and provider.models_called() == [MINI, MINI5]


def test_empty_answer_is_billed_and_falls_back(make_executor):
    ex, _ = make_executor({MINI: [Empty(completion_tokens=500)]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and r.fallbacks_used == 1
    failed = r.attempts[0]
    assert failed.error_category == "invalid_response" and failed.completion_tokens == 500
    assert failed.cost_usd == pytest.approx((100 * 0.40 + 500 * 1.60) / 1e6)
    assert r.total_cost_usd == pytest.approx(failed.cost_usd + r.result.cost_usd)


def test_all_fallbacks_exhausted(make_executor):
    script = {MINI: [err("server")] * 2, MINI5: [err("server")] * 2, FULL: [err("server")] * 2}
    ex, provider = make_executor(script, max_calls=10)
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.EXHAUSTED and r.result is None and len(provider.calls) == 6


def test_fallback_hops_are_capped(make_executor):
    ex, provider = make_executor({MINI: [err("not_found")], MINI5: [err("not_found")], FULL: [err("not_found")]},
                                 max_fallback_hops=1)
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.EXHAUSTED and provider.models_called() == [MINI, MINI5]


def test_total_call_limit_stops_retries_and_fallbacks(make_executor):
    script = {MINI: [err("server")] * 2, MINI5: [err("server")] * 2}
    ex, provider = make_executor(script, max_calls=3)
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.CALL_LIMIT and len(provider.calls) == 3


def test_calls_already_used_count_towards_the_limit(make_executor):
    ex, provider = make_executor({MINI: [err("server"), err("server")]}, max_calls=4)
    r = ex.run_chain(CHAIN, MSG, 200, calls_already_used=3)  # e.g. a classifier call happened first
    assert r.stop_reason == StopReason.CALL_LIMIT and len(provider.calls) == 1


def test_caller_cannot_extend_the_server_deadline(make_executor):
    ex, provider = make_executor(deadline_s=45)
    ex.run_chain(CHAIN, MSG, 200, deadline_s=600)
    assert provider.calls[0]["timeout"] == 20  # gpt-4.1-mini's own limit, inside the 45 s server deadline


def test_a_timeout_falls_back_at_once_instead_of_waiting_again(make_executor, clock):
    """Live finding: a reasoning model timed out, was retried, timed out again (40 s lost)."""
    ex, provider = make_executor({MINI: [err("timeout")]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and provider.models_called() == [MINI, MINI5] and clock.sleeps == []


def test_each_option_gets_its_own_time_limit(make_executor):
    ex, provider = make_executor({"openai/gpt-5-mini": [err("timeout")]}, deadline_s=200)
    ex.run_chain(["gpt-5-mini-high", "gpt-4.1"], MSG, 200)
    assert [c["timeout"] for c in provider.calls] == [75, 30]  # reasoning model gets longer to think


def test_slow_answer_inside_the_call_timeout_is_kept(make_executor):
    ex, _ = make_executor({MINI: [Slow(15, "late but valid")]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and r.result.text == "late but valid" and r.result.latency_ms == 15000


def test_deadline_stops_before_next_call(make_executor, clock):
    ex, provider = make_executor({MINI: [err("timeout")]})

    def slow_timeout(**kwargs):
        provider.calls.append(kwargs)
        clock.now += 50  # the call itself used up the deadline
        raise err("timeout")

    ex._completion = slow_timeout
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.DEADLINE and len(provider.calls) == 1


def test_per_call_timeout_never_exceeds_remaining_time(make_executor):
    ex, provider = make_executor()
    ex.run_chain(CHAIN, MSG, 200, deadline_s=5)
    assert provider.calls[0]["timeout"] == pytest.approx(5)


def test_budget_stops_before_an_unaffordable_call(make_executor):
    ex, provider = make_executor()
    r = ex.run_chain(["gpt-4.1"], MSG, max_tokens=800, max_cost_usd=0.0001)
    assert r.stop_reason == StopReason.BUDGET and provider.calls == []
    assert r.attempts[0].outcome == "skipped_budget"


def test_budget_counts_failed_billed_calls(make_executor):
    # gpt-4.1-mini empty answer bills 0.00004 + 0.0008; the fallback's worst case no longer fits
    ex, provider = make_executor({MINI: [Empty(500)]})
    r = ex.run_chain(CHAIN, MSG, max_tokens=200, max_cost_usd=0.001)
    assert r.stop_reason == StopReason.BUDGET and provider.models_called() == [MINI]


def test_open_breaker_skips_the_model(make_executor):
    ex, provider = make_executor()
    for _ in range(5):
        ex.breaker.record_failure("gpt-4.1-mini")
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.ok and r.result.option.id == "gpt-5-mini-low"
    assert r.attempts[0].outcome == "skipped_breaker" and provider.models_called() == [MINI5]


def test_repeated_failures_across_requests_open_the_breaker(make_executor):
    ex, provider = make_executor({MINI: [err("not_found")] * 5})
    for _ in range(5):
        ex.run_chain(CHAIN, MSG, 200)
    assert ex.breaker.is_open("gpt-4.1-mini")
    calls_before = len(provider.calls)
    ex.run_chain(CHAIN, MSG, 200)
    assert provider.models_called()[calls_before:] == [MINI5]


def test_missing_api_key_is_an_auth_error_and_stops_the_chain(make_executor):
    import litellm
    missing = litellm.InternalServerError("OpenAIException - The api_key client option must be set", "openai", "x")
    assert classify_error(missing) == ErrorCategory.AUTH
    ex, provider = make_executor({MINI: [missing]})
    r = ex.run_chain(CHAIN, MSG, 200)
    assert r.stop_reason == StopReason.NON_RETRYABLE and len(provider.calls) == 1


def test_failure_reason_is_logged_for_the_operator(make_executor, caplog):
    ex, _ = make_executor({MINI: [err("not_found")]})
    with caplog.at_level("WARNING", logger="model-router.executor"):
        ex.run_chain(CHAIN, MSG, 200)
    assert "option=gpt-4.1-mini category=not_found" in caplog.text and "no such model" in caplog.text
