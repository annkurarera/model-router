"""Routing policy: turns a request into an ordered model chain plus the reason for it.

- RulesPolicy (default, version rules-1): hand-written rules pick category and difficulty,
  and rules_policy.yaml maps them to a chain.
- StaticPolicy: one fixed chain for every request (kept for tests and as a baseline).
Session 6 adds the policy generated from benchmark results behind the same `decide()` interface.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .registry import Registry, RegistryError
from .rules import CATEGORIES, DIFFICULTIES, analyse, shift

log = logging.getLogger("model-router.policy")
TIER_ORDER = ["tiny", "mid", "strong", "reasoning"]


@dataclass
class Decision:
    chain: list[str]
    reason: str
    policy_version: str
    mode: str = "balanced"
    category: str | None = None
    difficulty: str | None = None
    confidence: float | None = None
    forced: bool = False
    compare: bool = False
    signals: list[str] = field(default_factory=list)


class StaticPolicy:
    version = "static-0"

    def __init__(self, registry: Registry, chain: list[str]):
        registry.validate_chain(chain)
        self.registry = registry
        self.chain = chain

    def decide(self, messages: list[dict], mode: str = "balanced") -> Decision:
        return Decision(
            chain=list(self.chain),
            reason="static default chain: the same models for every request",
            policy_version=self.version,
            mode=mode,
        )


class RulesPolicy:
    def __init__(self, registry: Registry, path: str | Path):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.registry = registry
        self.version = str(data["version"])
        self.min_structured_tier = data.get("structured_output_min_tier", "mid")
        self.chains: dict[str, dict[str, list[str]]] = {}
        for category in CATEGORIES:
            for difficulty in DIFFICULTIES:
                try:
                    raw = data["chains"][category][difficulty]
                except KeyError:
                    raise RegistryError(f"{path}: no chain for {category}/{difficulty}") from None
                usable = [o for o in raw if registry.has(o) and registry.get(o).enabled]
                dropped = [o for o in raw if o not in usable]
                if dropped:  # e.g. a model disabled because the account cannot use it
                    log.warning("rules policy %s/%s: skipping disabled or unknown %s", category, difficulty, dropped)
                if not usable:
                    raise RegistryError(f"{path}: every option for {category}/{difficulty} is disabled or unknown")
                self.chains.setdefault(category, {})[difficulty] = usable
        # Optional: chains for hard questions in quality mode (more thinking time)
        self.quality_hard: dict[str, list[str]] = {}
        for category, raw in (data.get("quality_hard") or {}).items():
            if category not in CATEGORIES:
                raise RegistryError(f"{path}: quality_hard has unknown category '{category}'")
            usable = [o for o in raw if registry.has(o) and registry.get(o).enabled]
            if usable:
                self.quality_hard[category] = usable

    def _tier_rank(self, option_id: str) -> int:
        return TIER_ORDER.index(self.registry.get(option_id).tier)

    def decide(self, messages: list[dict], mode: str = "balanced") -> Decision:
        a = analyse(messages)
        level = shift(a.difficulty, mode)
        chain = list(self.chains[a.category][level])
        notes = []
        if level != a.difficulty:
            notes.append(f"{mode} mode: routed as {level}")
        if mode == "quality" and level == "hard" and a.category in self.quality_hard:
            chain = list(self.quality_hard[a.category])
            notes.append("quality mode: longest thinking")
        if a.structured_output:
            floor = TIER_ORDER.index(self.min_structured_tier)
            if self._tier_rank(chain[0]) < floor:
                chain = [o for o in chain if self._tier_rank(o) >= floor] or chain
                notes.append(f"JSON requested: starts at {self.min_structured_tier} tier or above")
        signals = ", ".join(a.signals) if a.signals else "no difficulty signals"
        reason = f"rules: {a.category.replace('_', ' ')}, {a.difficulty} ({signals}) → {chain[0]}"
        if notes:
            reason += "; " + "; ".join(notes)
        return Decision(chain=chain, reason=reason, policy_version=self.version, mode=mode,
                        category=a.category, difficulty=a.difficulty, signals=a.signals)
