"""Shared test helpers: a manual clock and a scripted fake provider. No test calls a real model."""
import sys
from pathlib import Path

import litellm
import pytest

ROUTER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROUTER_DIR))

from app.breaker import CircuitBreaker  # noqa: E402
from app.config import Settings  # noqa: E402
from app.executor import ExecutionLimits, Executor  # noqa: E402
from app.registry import Registry  # noqa: E402

MODELS_FILE = ROUTER_DIR / "app" / "models.yaml"


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Slow:
    """Outcome that takes `seconds` of (fake) time, then answers."""

    def __init__(self, seconds: float, text: str = "slow answer"):
        self.seconds, self.text = seconds, text


class Empty:
    """Outcome with no content but billed tokens (a reasoning model that ran out of budget)."""

    def __init__(self, completion_tokens: int = 500):
        self.completion_tokens = completion_tokens


def err(kind: str, model: str = "openai/x"):
    """Real LiteLLM exception instances, so error classification is tested against the real classes."""
    p = "openai"
    return {
        "timeout": lambda: litellm.Timeout("timed out", model, p),
        "rate_limit": lambda: litellm.RateLimitError("429", p, model),
        "server": lambda: litellm.InternalServerError("500", p, model),
        "unavailable": lambda: litellm.ServiceUnavailableError("503", p, model),
        "connection": lambda: litellm.APIConnectionError("conn reset", p, model),
        "auth": lambda: litellm.AuthenticationError("bad key", p, model),
        "bad_request": lambda: litellm.BadRequestError("bad param", model, p),
        "not_found": lambda: litellm.NotFoundError("no such model", model, p),
        "context": lambda: litellm.ContextWindowExceededError("too long", model, p),
    }[kind]()


class FakeProvider:
    """Scripted replies per provider model string. Each call pops the next outcome:
    a string (answer), an Exception (raised), Slow(...) or Empty(...).
    When a model's script runs out it answers 'ok'."""

    def __init__(self, clock: FakeClock, script: dict[str, list] | None = None,
                 prompt_tokens: int = 100, completion_tokens: int = 50):
        self.clock = clock
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[dict] = []
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        queue = self.script.get(kwargs["model"], [])
        outcome = queue.pop(0) if queue else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Slow):
            self.clock.now += outcome.seconds
            outcome = outcome.text
        if isinstance(outcome, Empty):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": self.prompt_tokens, "completion_tokens": outcome.completion_tokens}}
        return {"choices": [{"message": {"content": outcome}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}}

    def models_called(self) -> list[str]:
        return [c["model"] for c in self.calls]


@pytest.fixture
def registry() -> Registry:
    return Registry.load(MODELS_FILE)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_executor(registry, clock):
    def _make(script=None, **limit_overrides):
        provider = FakeProvider(clock, script)
        limits = ExecutionLimits(**{**dict(per_call_timeout_s=20, deadline_s=45, max_calls=4, max_fallback_hops=2,
                                           max_retries_per_call=1, backoff_s=0.5), **limit_overrides})
        breaker = CircuitBreaker(5, 60, clock)
        return Executor(registry, breaker, limits, provider, clock, clock.sleep), provider
    return _make


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Most API tests use the static policy so their expectations do not depend on the routing rules;
    # test_rules.py covers the rules policy.
    return Settings(_env_file=None, models_file=str(MODELS_FILE), log_db_path=str(tmp_path / "logs.db"),
                    routing_policy="static",
                    require_api_key=False, client_api_keys="client-key", admin_api_keys="admin-key",
                    rate_limit_per_minute=100, per_client_daily_limit=1000, daily_budget_usd=5.0)
