"""Request and error shapes for the OpenAI-compatible API."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["system", "developer", "user", "assistant"]
    content: str | list[dict]


class RouterOptions(BaseModel):
    """Router-specific extension. Callers can only TIGHTEN server limits, never raise them."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["cheapest", "balanced", "quality"] = "balanced"
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_latency_ms: int | None = Field(default=None, gt=0)
    # Ask the reference (strongest) model the same question, to compare answers. The only model
    # a non-admin caller may get by name; rate limits and budgets still apply.
    compare_with_reference: bool = False


class ChatRequest(BaseModel):
    # OpenAI SDKs send extra fields (user, metadata, ...); unknown ones are ignored
    model_config = ConfigDict(extra="ignore")
    model: str = "auto"
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    response_format: dict | None = None
    stream: bool = False
    n: int = 1
    router: RouterOptions = Field(default_factory=RouterOptions)

    @property
    def requested_max_tokens(self) -> int | None:
        return self.max_completion_tokens or self.max_tokens


class RouterError(Exception):
    """Returned to the caller as an OpenAI-style error body."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": "router_error", "code": self.code, **self.extra}}
