"""Model pool: loads models.yaml, validates it, and does all cost arithmetic.

Costs are computed from token counts and the prices in models.yaml (never from a
provider's own estimate), so every number in the report traces back to one file.
"""
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

TIERS = {"tiny", "mid", "strong", "reasoning"}
EFFORTS = {None, "minimal", "low", "medium", "high"}
SEARCH_SIZES = {None, "low", "medium", "high"}


class RegistryError(ValueError):
    """models.yaml is missing a field or has an invalid value."""


@dataclass(frozen=True)
class ModelOption:
    id: str
    model: str
    tier: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    reasoning_effort: str | None = None
    supports_temperature: bool = True
    min_output_tokens: int = 0
    timeout_s: float | None = None
    demo_allowed: bool = True
    enabled: bool = True
    web_search: bool = False              # searches the web before answering (OpenAI search models)
    search_context_size: str | None = None  # low | medium | high: how much search content the model reads
    per_call_usd: float = 0.0             # fixed fee per call on top of tokens (web search is billed per call)

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Actual cost of one call from its token usage, plus any fixed per-call fee."""
        tokens = (prompt_tokens * self.input_usd_per_mtok + completion_tokens * self.output_usd_per_mtok) / 1_000_000
        return tokens + self.per_call_usd

    def output_budget(self, max_tokens: int) -> int:
        """Output tokens to request. Reasoning models need room to think before they answer."""
        return max(max_tokens, self.min_output_tokens)

    def worst_case_cost_usd(self, prompt_tokens: int, max_tokens: int) -> float:
        """Upper bound used for budget checks before calling: every allowed output token is billed."""
        return self.cost_usd(prompt_tokens, self.output_budget(max_tokens))


def estimate_prompt_tokens(messages: list[dict]) -> int:
    """Conservative token estimate without a tokenizer download: about 3 characters per token,
    plus a small overhead per message. Over-estimating is the safe side for budget checks."""
    chars = sum(len(_text_of(m.get("content"))) for m in messages)
    return math.ceil(chars / 3) + 8 * len(messages) + 8


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


class Registry:
    def __init__(self, options: list[ModelOption], price_checked: str = ""):
        ids = [o.id for o in options]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise RegistryError(f"Duplicate option ids: {sorted(dupes)}")
        self._options = {o.id: o for o in options}
        self.price_checked = price_checked

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = data.get("options")
        if not raw:
            raise RegistryError(f"{path}: no 'options' listed")
        return cls([cls._parse(entry) for entry in raw], str(data.get("price_checked", "")))

    @staticmethod
    def _parse(entry: dict) -> ModelOption:
        for field in ("id", "model", "tier", "input_usd_per_mtok", "output_usd_per_mtok"):
            if entry.get(field) in (None, ""):
                raise RegistryError(f"Option {entry.get('id', '?')}: missing '{field}'")
        if entry["tier"] not in TIERS:
            raise RegistryError(f"Option {entry['id']}: tier must be one of {sorted(TIERS)}")
        if entry.get("reasoning_effort") not in EFFORTS:
            raise RegistryError(f"Option {entry['id']}: invalid reasoning_effort {entry.get('reasoning_effort')!r}")
        if entry.get("timeout_s") is not None and entry["timeout_s"] <= 0:
            raise RegistryError(f"Option {entry['id']}: timeout_s must be positive")
        if entry["input_usd_per_mtok"] < 0 or entry["output_usd_per_mtok"] < 0 or entry.get("per_call_usd", 0) < 0:
            raise RegistryError(f"Option {entry['id']}: prices cannot be negative")
        if entry.get("search_context_size") not in SEARCH_SIZES:
            raise RegistryError(f"Option {entry['id']}: search_context_size must be low, medium or high")
        if entry.get("search_context_size") and not entry.get("web_search"):
            raise RegistryError(f"Option {entry['id']}: search_context_size needs web_search: true")
        known = set(ModelOption.__dataclass_fields__)
        unknown = set(entry) - known
        if unknown:
            raise RegistryError(f"Option {entry['id']}: unknown fields {sorted(unknown)}")
        return ModelOption(**entry)

    def get(self, option_id: str) -> ModelOption:
        try:
            return self._options[option_id]
        except KeyError:
            raise KeyError(f"Unknown model option '{option_id}'") from None

    def has(self, option_id: str) -> bool:
        return option_id in self._options

    def enabled(self) -> list[ModelOption]:
        return [o for o in self._options.values() if o.enabled]

    def validate_chain(self, chain: list[str]) -> None:
        """A chain may only contain known, enabled options."""
        for option_id in chain:
            if not self.has(option_id) or not self.get(option_id).enabled:
                raise RegistryError(f"Chain option '{option_id}' is unknown or disabled")
