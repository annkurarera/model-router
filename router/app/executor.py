"""Calls models with hard limits: per-call timeout, request deadline, bounded retries,
a bounded fallback chain, a total call limit, a per-request budget and a circuit breaker.

Nothing here can loop: every path either returns an answer or stops with a named reason.
The provider call is injectable (`completion_fn`) so tests use scripted fakes, not the API.
"""
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from .breaker import CircuitBreaker
from .registry import ModelOption, Registry, estimate_prompt_tokens


log = logging.getLogger("model-router.executor")


class ErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    CONNECTION = "connection"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    CONTEXT_LENGTH = "context_length"
    NOT_FOUND = "not_found"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


# Worth one retry on the same model. A timeout is NOT: a model that was too slow once will be too slow
# again, so the router moves straight to the next model instead of waiting a second time.
TRANSIENT = {ErrorCategory.RATE_LIMIT, ErrorCategory.SERVER_ERROR, ErrorCategory.CONNECTION}
# Fail the whole request: the next model would fail the same way (bad key, malformed request)
STOP_CHAIN = {ErrorCategory.AUTH, ErrorCategory.BAD_REQUEST}


class StopReason(str, Enum):
    OK = "ok"
    EXHAUSTED = "fallbacks_exhausted"
    DEADLINE = "deadline_exceeded"
    BUDGET = "budget_exhausted"
    CALL_LIMIT = "call_limit_reached"
    NON_RETRYABLE = "non_retryable_error"


class InvalidResponse(Exception):
    """The provider answered but the response has no usable content. The call may still be
    billed (a reasoning model can spend its whole budget thinking), so usage is kept."""

    def __init__(self, message: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd


def classify_error(exc: Exception) -> ErrorCategory:
    """Maps provider exceptions to categories by class name, checking subclasses first
    (LiteLLM's Timeout subclasses APIConnectionError; ContextWindowExceeded subclasses BadRequest)."""
    if isinstance(exc, InvalidResponse):
        return ErrorCategory.INVALID_RESPONSE
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT
    text = str(exc).lower()
    if "api_key" in text or "api key" in text:  # missing or malformed key, whatever class wraps it
        return ErrorCategory.AUTH
    names = {cls.__name__ for cls in type(exc).__mro__}
    ordered = [
        ({"Timeout", "APITimeoutError"}, ErrorCategory.TIMEOUT),
        ({"ContextWindowExceededError"}, ErrorCategory.CONTEXT_LENGTH),
        ({"RateLimitError"}, ErrorCategory.RATE_LIMIT),
        ({"AuthenticationError", "PermissionDeniedError"}, ErrorCategory.AUTH),
        ({"NotFoundError"}, ErrorCategory.NOT_FOUND),
        ({"BadRequestError", "UnprocessableEntityError", "UnsupportedParamsError"}, ErrorCategory.BAD_REQUEST),
        ({"InternalServerError", "ServiceUnavailableError"}, ErrorCategory.SERVER_ERROR),
        ({"APIConnectionError"}, ErrorCategory.CONNECTION),
    ]
    for keys, category in ordered:
        if names & keys:
            return category
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return ErrorCategory.SERVER_ERROR
    return ErrorCategory.UNKNOWN


@dataclass
class ExecutionLimits:
    per_call_timeout_s: float = 20.0
    deadline_s: float = 45.0
    max_calls: int = 4
    max_fallback_hops: int = 2
    max_retries_per_call: int = 1
    backoff_s: float = 0.5


@dataclass
class Attempt:
    option_id: str
    outcome: str                      # ok | error | skipped_breaker | skipped_budget
    error_category: str | None = None
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    retry: bool = False

    def public(self) -> dict:
        return {"model": self.option_id, "outcome": self.outcome, "error_category": self.error_category,
                "latency_ms": self.latency_ms, "retry": self.retry}


@dataclass
class CallResult:
    option: ModelOption
    text: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int


@dataclass
class ChainResult:
    stop_reason: StopReason
    result: CallResult | None = None
    attempts: list[Attempt] = field(default_factory=list)
    fallbacks_used: int = 0
    calls_made: int = 0
    error_category: ErrorCategory | None = None

    @property
    def ok(self) -> bool:
        return self.stop_reason == StopReason.OK

    @property
    def total_cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.attempts)


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def preload_litellm() -> None:
    """Load LiteLLM at startup instead of on the first request. It is a large library: on a small
    cloud instance the first import took most of a minute, and the first visitor waited for it.
    The bundled price map is used, so start-up never waits on a download (costs come from models.yaml)."""
    import os

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.drop_params = True  # silently drop params a model does not support
    litellm.suppress_debug_info = True  # no "Give Feedback" banner on errors


def _default_completion(**kwargs):
    import litellm  # already loaded by preload_litellm() in the server; tests never get here

    litellm.drop_params = True
    litellm.suppress_debug_info = True
    return litellm.completion(**kwargs)


class Executor:
    def __init__(self, registry: Registry, breaker: CircuitBreaker, limits: ExecutionLimits,
                 completion_fn=None, clock=time.monotonic, sleep=time.sleep):
        self.registry = registry
        self.breaker = breaker
        self.limits = limits
        self._completion = completion_fn or _default_completion
        self._clock = clock
        self._sleep = sleep

    # -- one call -------------------------------------------------------------------------
    def build_kwargs(self, option: ModelOption, messages: list[dict], max_tokens: int,
                     temperature: float | None, response_format: dict | None, timeout: float) -> dict:
        kwargs = {
            "model": option.model,
            "messages": messages,
            "max_tokens": option.output_budget(max_tokens),
            "timeout": timeout,
            "num_retries": 0,  # retries are decided here, not inside LiteLLM
        }
        if option.reasoning_effort:
            kwargs["reasoning_effort"] = option.reasoning_effort
        if temperature is not None and option.supports_temperature:
            kwargs["temperature"] = temperature
        if response_format:
            kwargs["response_format"] = response_format
        if option.web_search:
            kwargs["web_search_options"] = {"search_context_size": option.search_context_size or "low"}
        return kwargs

    def call_once(self, option: ModelOption, messages: list[dict], max_tokens: int,
                  temperature: float | None, response_format: dict | None, timeout: float) -> CallResult:
        start = self._clock()
        response = self._completion(**self.build_kwargs(option, messages, max_tokens, temperature,
                                                        response_format, timeout))
        latency_ms = int((self._clock() - start) * 1000)
        choices = _get(response, "choices") or []
        if not choices:
            raise InvalidResponse("no choices in response")
        message = _get(choices[0], "message")
        text = _get(message, "content") if message is not None else None
        finish_reason = _get(choices[0], "finish_reason")
        usage = _get(response, "usage") or {}
        prompt_tokens = int(_get(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(_get(usage, "completion_tokens", 0) or 0)
        cost = option.cost_usd(prompt_tokens, completion_tokens)
        if not text or not str(text).strip():
            # e.g. a reasoning model that spent its whole output budget thinking: billed, but unusable
            raise InvalidResponse(f"empty content (finish_reason={finish_reason})",
                                  prompt_tokens, completion_tokens, cost)
        return CallResult(option, str(text), finish_reason, prompt_tokens, completion_tokens, cost, latency_ms)

    # -- the bounded chain ----------------------------------------------------------------
    def run_chain(self, chain: list[str], messages: list[dict], max_tokens: int,
                  temperature: float | None = None, response_format: dict | None = None,
                  max_cost_usd: float | None = None, deadline_s: float | None = None,
                  calls_already_used: int = 0) -> ChainResult:
        """Try the chain in order: primary first, then at most `max_fallback_hops` fallbacks."""
        limits = self.limits
        deadline = self._clock() + min(deadline_s or limits.deadline_s, limits.deadline_s)
        prompt_tokens_est = estimate_prompt_tokens(messages)
        out = ChainResult(stop_reason=StopReason.EXHAUSTED, calls_made=0)
        calls = calls_already_used
        spent = 0.0

        for position, option_id in enumerate(chain[: 1 + limits.max_fallback_hops]):
            option = self.registry.get(option_id)
            if self.breaker.is_open(option_id):
                out.attempts.append(Attempt(option_id, "skipped_breaker"))
                continue
            retries = 0
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return self._stop(out, StopReason.DEADLINE, calls - calls_already_used)
                if calls >= limits.max_calls:
                    return self._stop(out, StopReason.CALL_LIMIT, calls - calls_already_used)
                if max_cost_usd is not None:
                    worst = option.worst_case_cost_usd(prompt_tokens_est, max_tokens)
                    if spent + worst > max_cost_usd:
                        out.attempts.append(Attempt(option_id, "skipped_budget"))
                        # Fallbacks are never cheaper than the primary in a cost-ordered chain: stop.
                        return self._stop(out, StopReason.BUDGET, calls - calls_already_used)
                calls += 1
                started = self._clock()
                try:
                    result = self.call_once(option, messages, max_tokens, temperature, response_format,
                                            timeout=min(option.timeout_s or limits.per_call_timeout_s, remaining))
                except Exception as exc:  # noqa: BLE001 - every provider failure is categorised
                    category = classify_error(exc)
                    # The reason goes to the server log only; callers get the category
                    log.warning("model call failed: option=%s category=%s %s: %s", option_id, category.value,
                                type(exc).__name__, str(exc).strip().splitlines()[0][:300] if str(exc).strip() else "")
                    self.breaker.record_failure(option_id)
                    billed = getattr(exc, "cost_usd", 0.0) if isinstance(exc, InvalidResponse) else 0.0
                    spent += billed
                    out.attempts.append(Attempt(
                        option_id, "error", category.value, int((self._clock() - started) * 1000),
                        getattr(exc, "prompt_tokens", 0) if billed else 0,
                        getattr(exc, "completion_tokens", 0) if billed else 0,
                        billed, retry=retries > 0))
                    out.error_category = category
                    if category in STOP_CHAIN:
                        return self._stop(out, StopReason.NON_RETRYABLE, calls - calls_already_used)
                    if category in TRANSIENT and retries < limits.max_retries_per_call:
                        retries += 1
                        wait = min(limits.backoff_s * (2 ** (retries - 1)), max(0.0, deadline - self._clock()))
                        self._sleep(wait)
                        continue
                    break  # move to the next option in the chain
                self.breaker.record_success(option_id)
                spent += result.cost_usd
                out.attempts.append(Attempt(option_id, "ok", None, result.latency_ms, result.prompt_tokens,
                                            result.completion_tokens, result.cost_usd, retry=retries > 0))
                out.result = result
                out.fallbacks_used = position
                out.error_category = None
                return self._stop(out, StopReason.OK, calls - calls_already_used)

        return self._stop(out, StopReason.EXHAUSTED, calls - calls_already_used)

    @staticmethod
    def _stop(out: ChainResult, reason: StopReason, calls_made: int) -> ChainResult:
        out.stop_reason = reason
        out.calls_made = calls_made
        return out
