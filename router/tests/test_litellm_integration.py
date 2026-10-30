"""Runs the executor through the REAL LiteLLM library in its offline mock mode
(mock_response), so response parsing and parameter handling are checked against real
LiteLLM objects. No network call and no API key are needed."""
import pytest

from app.breaker import CircuitBreaker
from app.executor import ExecutionLimits, Executor, StopReason, _default_completion

MSG = [{"role": "user", "content": "Classify: I was charged twice"}]


def _executor(registry, mock):
    return Executor(registry, CircuitBreaker(), ExecutionLimits(),
                    lambda **kw: _default_completion(**kw, mock_response=mock))


@pytest.mark.parametrize("option", ["gpt-4.1-mini", "gpt-5-mini-high", "gpt-5-nano-minimal", "gpt-4.1"])
def test_real_litellm_response_is_parsed_for_every_option_type(registry, option):
    r = _executor(registry, "Card dispute").run_chain([option], MSG, 200, temperature=0.3)
    assert r.stop_reason == StopReason.OK and r.result.text == "Card dispute"
    assert r.result.prompt_tokens > 0 and r.result.completion_tokens > 0
    assert r.result.cost_usd == pytest.approx(
        registry.get(option).cost_usd(r.result.prompt_tokens, r.result.completion_tokens))


def test_real_litellm_exception_triggers_fallback(registry):
    import litellm

    calls = []

    def flaky(**kw):
        calls.append(kw["model"])
        if kw["model"] == "openai/gpt-4.1-mini":
            # LiteLLM turns this mock into a real RateLimitError
            return _default_completion(**kw, mock_response=litellm.RateLimitError("429", "openai", kw["model"]))
        return _default_completion(**kw, mock_response="fallback answer")

    ex = Executor(registry, CircuitBreaker(), ExecutionLimits(backoff_s=0), flaky)
    r = ex.run_chain(["gpt-4.1-mini", "gpt-4.1"], MSG, 200)
    assert r.ok and r.result.text == "fallback answer" and r.fallbacks_used == 1
    assert calls == ["openai/gpt-4.1-mini", "openai/gpt-4.1-mini", "openai/gpt-4.1"]
